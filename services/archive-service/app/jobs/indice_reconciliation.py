# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Job de reconciliación del sellado del índice electrónico (residual Ac.
AGN 001/2024 art. 4.3.2.4) — converge los índices que quedaron
'pendiente_firma' tras `ArchiveService.close_expediente`
(`IndexService.firmar_indice_close` agotó su control compensatorio síncrono,
o el sello institucional estaba ausente en ese instante) llamando a la ruta
INTERNA de signature-service (`POST /internal/sign-indice`, atribuida a
`SYSTEM_RECONCILER_ID`) con backoff exponencial por índice.

Espejo de `signature-service/app/jobs/deadletter_retention.py`: mismo patrón
per-tenant (`list_active_tenants` desde `public.tenants`), aislamiento de
fallos por-tenant/por-índice (WARNING, no aborta el resto), `run_once()` +
entry-point standalone `python -m app.jobs.indice_reconciliation`.

Concurrencia entre réplicas/instancias del servicio: `pg_try_advisory_lock`
por tenant — si otra instancia ya sostiene el lock para este tenant, ESTA
barrida lo SALTA (no espera, no reintenta dentro del mismo `run_once`) en vez
de arriesgar sellar el MISMO índice en paralelo desde dos procesos. La
idempotencia REAL la garantiza `IndexRepository.mark_signed` (`WHERE estado
<> 'firmado'`) — el advisory lock es solo para no malgastar cupo HTTP en
trabajo redundante, no la única defensa (ver
`tests/integration/test_indice_reconciliation_atomicity.py`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import asyncpg
from orpycamcp_common import audit

from app.clients import signature_client
from app.clients.notification_client import NotificationDeliveryError, enviar_aviso
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import acquire_tenant, close_pool, create_pool, get_pool
from app.core.disposition import CT_DISPOSITION_CODES
from app.repositories.aip_repository import AipRepository
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.aip_wiring_service import AipWiringService
from app.services.archive_service import ArchiveService
from app.services.backoff import next_backoff_seconds
from app.services.index_service import IndexService
from app.services.index_xsd import MOTIVO_XSD_INVALIDO, validar_indice_xml
from app.services.transferencia_service import TransferenciaService
from app.services.valoracion_service import ValoracionService

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"


async def list_active_tenants(pool: asyncpg.Pool) -> list[str]:
    """Slugs de los tenants con `status='active'` y `provisioning_status='active'`.

    Mismas columnas que `signature-service/app/jobs/deadletter_retention.py`
    (verificadas contra las migraciones de tenant-service:
    `001_create_tenants.sql` → slug, status; `002_provisioning_status.sql` →
    provisioning_status).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug FROM public.tenants "
            "WHERE status = 'active' AND provisioning_status = 'active'"
        )
    return [row["slug"] for row in rows]


async def list_tenants_conservacion(pool: asyncpg.Pool) -> list[str]:
    """Entidades `active` y `suspended` (aprovisionadas) para las fases de CONSERVACIÓN
    (WORM, renovación de retención, fixity). El empaquetado del AIP NO está entre ellas.

    La suspensión detiene la operación de una entidad, no su obligación de conservar (Ley 594
    art. 46; Ac. AGN 003/2015; ADR-035, enmienda de B10): los plazos WORM, el AIP y la fixity siguen
    corriendo. `inactive` (dada de baja) y las sin aprovisionar quedan fuera. Los trabajos que
    OPERAN (sellar índices y actas, origen legado) siguen con `list_active_tenants`."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug FROM public.tenants "
            "WHERE status IN ('active', 'suspended') AND provisioning_status = 'active'"
        )
    return [row["slug"] for row in rows]


def _next_backoff_seconds(intentos_tras_este_fallo: int) -> int:
    """`min(cap, base * 2**intentos)` — mismo patrón de backoff exponencial
    documentado en el contrato (E06/ADR-021 Fase 4 usa la misma fórmula para
    el DLQ). Delgado sobre `app.services.backoff.next_backoff_seconds`
    (extraído para que `IndexService.proteger_worm_indice` también la use,
    ver ese módulo) con los settings de ESTE job."""
    return next_backoff_seconds(
        intentos_tras_este_fallo,
        base=settings.indice_reconciliation_backoff_base_seconds,
        cap=settings.indice_reconciliation_backoff_cap_seconds,
    )


async def _reconciliar_indice(
    conn: asyncpg.Connection, slug: str, row: asyncpg.Record
) -> str:
    """Intenta sellar UN índice 'pendiente_firma'. Devuelve 'sellado',
    'fallido' (backoff programado) o 'agotado' (`reconcile_agotado=TRUE`,
    permanece 'pendiente_firma' hasta intervención manual — NUNCA se inventa
    conformidad)."""
    repo = IndexRepository(conn)
    # AREA-07-01: el XSD se valida ANTES de pedir el sello también aquí, porque este
    # job firma por su cuenta (`sign_indice_internal`) sin pasar por `IndexService`.
    # Un índice que no valida no se sella: sigue el camino de fallo (backoff y, al
    # agotarse, aviso) con el motivo `xsd_invalido`.
    error_xsd = validar_indice_xml(row["xml"])
    if error_xsd is not None:
        logger.warning(
            "Índice %s (tenant=%s) no valida contra su XSD: %s", row["id"], slug, error_xsd
        )
        outcome = None
    else:
        outcome = await signature_client.sign_indice_internal(
            tenant_slug=slug,
            payload_xml=row["xml"],
            objeto_id=str(row["expediente_id"]),
            indice_id=str(row["id"]),
            version=row["version"],
        )

    if outcome is not None and outcome.signed is not None:
        async with conn.transaction():
            affected = await repo.mark_signed(
                row["id"], outcome.signed.firma_id, outcome.signed.minio_key_firmado
            )
            # `affected == 0`: otra conexión (otra instancia del job, o un
            # reintento manual concurrente vía POST /indice/firmar) ya firmó
            # esta MISMA fila entre el momento en que se listó como
            # pendiente y este UPDATE — el `WHERE estado <> 'firmado'` de
            # `mark_signed` no casó ninguna fila. NO se escribe un asiento
            # `archive.indice_firmado` fantasma sobre un efecto que ESTA
            # llamada no produjo (idempotencia real, no solo del UPDATE en
            # sí — también de la traza de auditoría que lo acompaña).
            if affected:
                await audit.append(
                    conn,
                    tenant_slug=slug,
                    service=_SERVICE,
                    action="archive.indice_firmado",
                    object_type="expediente_indice",
                    object_ref=row["expediente_code"],
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "expediente_id": str(row["expediente_id"]),
                        "firma_id": str(outcome.signed.firma_id),
                        "origen": "reconciliation",
                    },
                )
        return "sellado"

    intentos_tras_este = int(row["reconcile_intentos"]) + 1
    agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
    # C2: mismo motivo textual que `IndexService.firmar_indice_close` usa
    # para la misma condición (sello institucional ausente) — antes este job
    # escribía el código de error de TRANSPORTE de signature-service
    # (`signing_seal_unavailable`, ver `signature_client._outcome_from_response`)
    # en vez del motivo de DOMINIO, dejando `reconcile_ultimo_error` y el
    # asiento `archive.indice_reconcile_agotado` inconsistentes con
    # `archive.indice_firma_pendiente` para la MISMA causa raíz.
    if outcome is None:
        motivo = MOTIVO_XSD_INVALIDO
    else:
        motivo = "sello_ausente" if outcome.seal_absent else "sellado_xades_no_obtenido"
    proximo_at = (
        None
        if agotado
        else datetime.now(tz=UTC) + timedelta(seconds=_next_backoff_seconds(intentos_tras_este))
    )

    if agotado:
        # S2: `bump_intento` (marca `reconcile_agotado=TRUE`) y su asiento
        # DEDICADO se comprometen juntos (mismo patrón H5 que
        # `IndexService._mark_pendiente_audited`) — antes eran dos commits
        # autocommit separados: un proceso muerto entre ambos dejaba
        # `reconcile_agotado=TRUE` (índice invisible para el job, exige
        # intervención manual) SIN el asiento que explica por qué, rompiendo
        # la trazabilidad probatoria de ese instante.
        async with conn.transaction():
            await repo.bump_intento(
                row["id"], proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
            )
            # Asiento DEDICADO (distinto de 'archive.indice_firma_pendiente', que
            # ya se emitió al degradar en el cierre): marca el instante en que el
            # job DEJA de reintentar automáticamente — visibilidad operacional
            # explícita de que este índice ahora exige intervención manual
            # (POST /indice/firmar), no solo silencio en los logs del job.
            await audit.append(
                conn,
                tenant_slug=slug,
                service=_SERVICE,
                action="archive.indice_reconcile_agotado",
                object_type="expediente_indice",
                object_ref=row["expediente_code"],
                actor=str(SYSTEM_RECONCILER_ID),
                canal="system",
                payload={
                    "expediente_id": str(row["expediente_id"]),
                    "reconcile_intentos": intentos_tras_este,
                    "motivo": motivo,
                },
            )
        # AREA-07-03: el asiento de arriba deja constancia, pero nadie lo lee solo.
        # El aviso va DESPUÉS del commit (el correo no se puede deshacer) y es
        # best-effort: que no salga no revierte el estado `agotado`.
        await _avisar_indice_agotado(conn, slug, row)
        return "agotado"

    await repo.bump_intento(row["id"], proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado)
    return "fallido"


_ASUNTO_INDICE_AGOTADO = "Índice electrónico sin sellar: requiere intervención"


def _cuerpo_indice_agotado() -> str:
    """Sin números de radicado, asuntos ni códigos de expediente: el correo sale de
    la institución (mismo criterio que el aviso de disposición vencida)."""
    return (
        "Un índice electrónico de expediente agotó los reintentos automáticos de "
        "sellado y sigue sin firmar. Un expediente con el índice sin firmar no puede "
        "transferirse.\n\n"
        "Puede revisarlo en Administración → Preservación, sección «Índices pendientes "
        "de firma», y reintentar el sellado desde allí una vez resuelta la causa."
    )


async def _avisar_indice_agotado(
    conn: asyncpg.Connection, slug: str, row: asyncpg.Record
) -> int:
    """Avisa a quien puede repararlo (los mismos permisos que `POST /indice/firmar` y
    el clearance del expediente). Devuelve cuántos avisos salieron; NUNCA lanza."""
    enviados = 0
    try:
        destinatarios = await ArchiveRepository(conn, slug).list_destinatarios_indice_agotado(
            row["expediente_id"]
        )
        for d in destinatarios:
            try:
                await enviar_aviso(
                    recipient_email=d["email"],
                    subject=_ASUNTO_INDICE_AGOTADO,
                    body=_cuerpo_indice_agotado(),
                    tenant_slug=slug,
                )
                enviados += 1
            except NotificationDeliveryError as exc:
                logger.warning(
                    "Aviso de índice agotado: no se pudo avisar en tenant=%s: %s", slug, exc
                )
    except Exception:  # noqa: BLE001 — best-effort: el aviso no revierte el estado.
        logger.warning(
            "Aviso de índice agotado: fallo resolviendo destinatarios tenant=%s", slug,
            exc_info=True,
        )
    return enviados


async def run_once() -> dict[str, int]:
    """Una barrida completa: por cada tenant activo, hasta
    `indice_reconciliation_batch_size` índices 'pendiente_firma' elegibles.
    Fallos por-tenant o por-índice quedan aislados (WARNING, no abortan el
    resto de la barrida)."""
    pool = get_pool()
    slugs = await list_active_tenants(pool)
    summary = {"pendientes_vistos": 0, "sellados": 0, "fallidos": 0, "agotados": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                # S3: forma de DOS claves (`pg_try_advisory_lock(k1, k2)`) —
                # `k1` es un namespace FIJO propio de este job (evita
                # colisionar con CUALQUIER OTRO usuario de advisory locks del
                # servicio, p. ej. `ArchiveRepository.link_radicado`, que usa
                # `hashtext(expediente_id)` de 32-bit en su propio espacio de
                # UN solo argumento); `k2` es `hashtext(slug)`. Con la forma
                # de UN argumento, dos slugs cuyo hashtext de 32-bit colisiona
                # comparten lock sin necesidad — con dos claves, esa colisión
                # solo puede ocurrir ENTRE slugs de este mismo job (mismo k1),
                # nunca contra otro subsistema. La colisión slug-vs-slug en
                # `k2` (32-bit) sigue siendo posible en teoría: en ese caso
                # esta barrida simplemente SALTA un tenant que no debía saltar
                # (mismo efecto que "otra instancia ya lo sostiene"), corregido
                # en la siguiente barrida — SIN impacto de correctitud, porque
                # la idempotencia real la garantiza `mark_signed WHERE estado
                # <> 'firmado'` (ver docstring del módulo), no este lock.
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:indice_reconcile'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    logger.debug(
                        "Reconciliación del índice: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )
                    continue
                try:
                    rows = await IndexRepository(conn).list_pending_signature(
                        settings.indice_reconciliation_batch_size
                    )
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        try:
                            resultado = await _reconciliar_indice(conn, slug, row)
                        except Exception:
                            logger.warning(
                                "Fallo reconciliando indice_id=%s (expediente_id=%s) "
                                "tenant=%s — se omite este índice en esta barrida",
                                row["id"],
                                row["expediente_id"],
                                slug,
                                exc_info=True,
                            )
                            summary["fallidos"] += 1
                            continue
                        if resultado == "sellado":
                            summary["sellados"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:indice_reconcile'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación del índice para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )

    logger.info(
        "Reconciliación del sellado del índice completada: "
        "pendientes_vistos=%d sellados=%d fallidos=%d agotados=%d tenants=%d",
        summary["pendientes_vistos"],
        summary["sellados"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


# ---------------------------------------------------------------------------
# Segunda fase — sellado del ACTA DE TRANSFERENCIA (H-H, E06/F4)
# ---------------------------------------------------------------------------
#
# Gemela de la fase del índice de arriba, para `transferencia_acta_firma`
# (migración 026) en vez de `expediente_indice`. Se mantiene como función
# INDEPENDIENTE (`run_once_actas`, no fusionada dentro de `run_once`) por dos
# motivos: (1) cadencia PROPIA y MENOR (ver
# `settings.acta_reconciliation_interval_seconds`, `app/main.py` la agenda en
# un loop de fondo aparte) — el sellado del acta es aditivo (el acta ya es
# íntegra y su SHA está anclado en `audit_log` desde
# `TransferenciaService.recibir`), menos urgente que el índice; (2) preserva
# INTACTA la superficie de `run_once()` que ya cubren
# `tests/test_indice_reconciliation.py` (conteos exactos del summary,
# aislamiento por-tenant/por-índice) — fusionarlas habría obligado a
# namespacar las claves del summary o arriesgado alterar ese contrato ya
# probado. Advisory lock en un namespace DISTINTO
# (`orpycamcp:archive:acta_reconcile`) para no colisionar con la barrida del
# índice si ambos loops coincidieran en el mismo instante.


async def _reconciliar_acta(conn: asyncpg.Connection, slug: str, row: asyncpg.Record) -> str:
    """Intenta sellar UN acta 'pendiente_firma' (gemela de
    `_reconciliar_indice`). Devuelve 'sellado', 'fallido' (backoff
    programado) o 'agotado' (`reconcile_agotado=TRUE`, permanece
    'pendiente_firma' hasta reintento manual — NUNCA se inventa
    conformidad)."""
    repo = TransferenciaRepository(conn, slug)
    outcome = await signature_client.sign_indice_internal(
        tenant_slug=slug,
        payload_xml=row["acta_fuid_xml"],
        objeto_id=str(row["transferencia_id"]),
        indice_id=None,
        version=row["indice_version"] or 0,
        objeto_tipo="acta_transferencia",
    )

    if outcome.signed is not None:
        async with conn.transaction():
            affected = await repo.mark_acta_firmada(
                row["transferencia_id"],
                firma_id=outcome.signed.firma_id,
                minio_key=outcome.signed.minio_key_firmado,
                xades_level=outcome.signed.xades_level,
                acreditado=outcome.signed.acreditado,
            )
            # Idempotencia real (gemela de `_reconciliar_indice`): `affected
            # == 0` -> otra conexión (otra instancia del job, o un reintento
            # manual concurrente vía POST /transferencias/{id}/acta/firmar)
            # ya firmó esta MISMA fila entre el listado y este UPDATE — NO se
            # escribe un asiento `archive.acta_firmada` fantasma.
            if affected:
                await audit.append(
                    conn,
                    tenant_slug=slug,
                    service=_SERVICE,
                    action="archive.acta_firmada",
                    object_type="transferencia_acta",
                    object_ref=str(row["transferencia_id"]),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "transferencia_id": str(row["transferencia_id"]),
                        "firma_id": str(outcome.signed.firma_id),
                        "origen": "reconciliation",
                    },
                )
        return "sellado"

    intentos_tras_este = int(row["reconcile_intentos"]) + 1
    agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
    motivo = "sello_ausente" if outcome.seal_absent else "sellado_xades_no_obtenido"
    proximo_at = (
        None
        if agotado
        else datetime.now(tz=UTC) + timedelta(seconds=_next_backoff_seconds(intentos_tras_este))
    )

    if agotado:
        async with conn.transaction():
            await repo.bump_acta_firma_intento(
                row["transferencia_id"], proximo_at=proximo_at, last_error=motivo, agotado=agotado
            )
            await audit.append(
                conn,
                tenant_slug=slug,
                service=_SERVICE,
                action="archive.acta_reconcile_agotado",
                object_type="transferencia_acta",
                object_ref=str(row["transferencia_id"]),
                actor=str(SYSTEM_RECONCILER_ID),
                canal="system",
                payload={
                    "transferencia_id": str(row["transferencia_id"]),
                    "reconcile_intentos": intentos_tras_este,
                    "motivo": motivo,
                },
            )
        return "agotado"

    await repo.bump_acta_firma_intento(
        row["transferencia_id"], proximo_at=proximo_at, last_error=motivo, agotado=agotado
    )
    return "fallido"


async def run_once_actas() -> dict[str, int]:
    """Una barrida completa de sellado de ACTAS 'pendiente_firma' (H-H,
    E06/F4) — gemela de `run_once()` (índice). Fallos por-tenant o por-acta
    quedan aislados (WARNING, no abortan el resto de la barrida)."""
    pool = get_pool()
    slugs = await list_active_tenants(pool)
    summary = {"pendientes_vistos": 0, "sellados": 0, "fallidos": 0, "agotados": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:acta_reconcile'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    logger.debug(
                        "Reconciliación del acta: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )
                    continue
                try:
                    rows = await TransferenciaRepository(conn, slug).list_acta_firmas_pendientes(
                        settings.indice_reconciliation_batch_size
                    )
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        try:
                            resultado = await _reconciliar_acta(conn, slug, row)
                        except Exception:
                            logger.warning(
                                "Fallo reconciliando acta de transferencia_id=%s tenant=%s "
                                "— se omite esta acta en esta barrida",
                                row["transferencia_id"],
                                slug,
                                exc_info=True,
                            )
                            summary["fallidos"] += 1
                            continue
                        if resultado == "sellado":
                            summary["sellados"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:acta_reconcile'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación del acta para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )

    logger.info(
        "Reconciliación del sellado del acta completada: "
        "pendientes_vistos=%d sellados=%d fallidos=%d agotados=%d tenants=%d",
        summary["pendientes_vistos"],
        summary["sellados"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


# ---------------------------------------------------------------------------
# Tercera fase — cableado WORM/Object-Lock del ÍNDICE (E10 Inc.1)
# ---------------------------------------------------------------------------
#
# Converge los índices que quedaron 'firmado' (E06 Inc.1) sin que su copia
# WORM en storage-service se haya confirmado todavía (el disparo síncrono
# tras el sellado, dentro de `IndexService.firmar_indice_close`, es
# best-effort y puede fallar por lo mismo que cualquier llamada HTTP:
# storage-service inalcanzable, PRESERVATION_ENABLED=false en ese instante,
# TRD sin retención resoluble). Independiente de `run_once()` (fase del
# SELLADO): un índice puede estar 'firmado' y con
# `reconcile_agotado=TRUE` del sellado sin que eso afecte esta fase —
# `worm_reconcile_*` es un eje de backoff completamente aparte
# (`worm_protegido_at`/`worm_reconcile_*`, migración 029).
#
# Reutiliza `IndexService.proteger_worm_indice` (el MISMO método que usa el
# disparo síncrono del cierre, con `origen="reconciliation"`) para no
# duplicar la derivación de retención TRD ni la marca atómica+asiento —
# `_reconciliar_worm` es un adaptador delgado que solo interpreta el
# resultado devuelto para el `summary` del barrido, mismo rol que
# `_reconciliar_indice`/`_reconciliar_acta` arriba.


async def _reconciliar_worm(conn: asyncpg.Connection, slug: str, row: asyncpg.Record) -> str:
    """Intenta proteger bajo WORM UN índice 'firmado' pendiente. Devuelve
    'protegido', 'fallido'/'pendiente' (backoff programado) o 'agotado'
    (`worm_reconcile_agotado=TRUE`, permanece sin proteger hasta
    intervención manual — NUNCA se inventa conformidad)."""
    index = IndexService(conn, slug)
    resultado = await index.proteger_worm_indice(
        row["expediente_id"], row["id"], actor_id=None, origen="reconciliation"
    )
    return resultado


async def run_once_worm() -> dict[str, int]:
    """Una barrida completa: por cada tenant activo, hasta
    `indice_reconciliation_batch_size` índices 'firmado' sin proteger bajo
    WORM. Fallos por-tenant o por-índice quedan aislados (WARNING, no
    abortan el resto de la barrida) — mismo patrón que `run_once()`."""
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    summary = {"pendientes_vistos": 0, "protegidos": 0, "fallidos": 0, "agotados": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                # Namespace de advisory lock PROPIO (`worm_reconcile`, distinto
                # de `indice_reconcile`/`acta_reconcile`) — esta fase puede
                # correr en el mismo ciclo que `run_once()` sin competir por
                # el mismo lock.
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:worm_reconcile'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    logger.debug(
                        "Reconciliación WORM: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )
                    continue
                try:
                    rows = await IndexRepository(conn).list_worm_pending(
                        settings.indice_reconciliation_batch_size
                    )
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        try:
                            resultado = await _reconciliar_worm(conn, slug, row)
                        except Exception:
                            logger.warning(
                                "Fallo reconciliando WORM indice_id=%s (expediente_id=%s) "
                                "tenant=%s — se omite este índice en esta barrida",
                                row["id"],
                                row["expediente_id"],
                                slug,
                                exc_info=True,
                            )
                            summary["fallidos"] += 1
                            continue
                        if resultado == "protegido":
                            summary["protegidos"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:worm_reconcile'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación WORM para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )

    logger.info(
        "Reconciliación WORM del índice completada: "
        "pendientes_vistos=%d protegidos=%d fallidos=%d agotados=%d tenants=%d",
        summary["pendientes_vistos"],
        summary["protegidos"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


# ---------------------------------------------------------------------------
# Cuarta fase — cableado WORM/Object-Lock del ACTA (E10 Inc.2 wiring)
# ---------------------------------------------------------------------------
#
# Converge las actas que quedaron 'firmado' (H-H, E06/F4) sin que su copia
# WORM en storage-service se haya confirmado todavía (el disparo síncrono tras
# el sellado, dentro de `TransferenciaService._sellar_acta_post_commit`, es
# best-effort y puede fallar por lo mismo que cualquier llamada HTTP:
# storage-service inalcanzable, PRESERVATION_ENABLED=false en ese instante,
# TRD sin retención resoluble). Gemela EXACTA de la tercera fase
# (`run_once_worm`, índice) para `transferencia_acta_firma` (migración 030)
# en vez de `expediente_indice` — fast-follow declarado en el diseño de
# Inc.1 ("Acta = generalizar"). Se agenda en el MISMO ciclo/cadencia que
# `run_once_actas` (`app/main.py::_acta_reconciliation_loop`,
# `settings.acta_reconciliation_interval_seconds`) — mismo criterio que ató
# `run_once_worm` al ciclo del índice: tan estrechamente acoplada a "el acta
# ya quedó 'firmado'" que no amerita kill-switch/intervalo propio.
#
# Reutiliza `TransferenciaService.proteger_worm_acta` (el MISMO método que
# usa el disparo síncrono post-commit, con `origen="reconciliation"`) para no
# duplicar la derivación de retención TRD ni la marca atómica+asiento —
# `_reconciliar_worm_acta` es un adaptador delgado, mismo rol que
# `_reconciliar_worm` arriba.


async def _reconciliar_worm_acta(conn: asyncpg.Connection, slug: str, row: asyncpg.Record) -> str:
    """Intenta proteger bajo WORM UNA acta 'firmado' pendiente. Devuelve
    'protegido', 'fallido'/'pendiente' (backoff programado) o 'agotado'
    (`worm_reconcile_agotado=TRUE`, permanece sin proteger hasta
    intervención manual — NUNCA se inventa conformidad)."""
    service = TransferenciaService(conn, slug)
    return await service.proteger_worm_acta(
        row["transferencia_id"], actor_id=None, origen="reconciliation"
    )


async def run_once_actas_worm() -> dict[str, int]:
    """Una barrida completa: por cada tenant activo, hasta
    `indice_reconciliation_batch_size` actas 'firmado' sin proteger bajo
    WORM. Fallos por-tenant o por-acta quedan aislados (WARNING, no abortan
    el resto de la barrida) — mismo patrón que `run_once_worm`."""
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    summary = {"pendientes_vistos": 0, "protegidos": 0, "fallidos": 0, "agotados": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                # Namespace de advisory lock PROPIO (`acta_worm_reconcile`,
                # distinto de `indice_reconcile`/`acta_reconcile`/
                # `worm_reconcile`) — esta fase puede correr en el mismo ciclo
                # que `run_once_actas()` sin competir por el mismo lock.
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:acta_worm_reconcile'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    logger.debug(
                        "Reconciliación WORM del acta: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )
                    continue
                try:
                    rows = await TransferenciaRepository(conn, slug).list_worm_pending_actas(
                        settings.indice_reconciliation_batch_size
                    )
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        try:
                            resultado = await _reconciliar_worm_acta(conn, slug, row)
                        except Exception:
                            logger.warning(
                                "Fallo reconciliando WORM acta de transferencia_id=%s "
                                "tenant=%s — se omite esta acta en esta barrida",
                                row["transferencia_id"],
                                slug,
                                exc_info=True,
                            )
                            summary["fallidos"] += 1
                            continue
                        if resultado == "protegido":
                            summary["protegidos"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:acta_worm_reconcile'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación WORM del acta para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )

    logger.info(
        "Reconciliación WORM del acta completada: "
        "pendientes_vistos=%d protegidos=%d fallidos=%d agotados=%d tenants=%d",
        summary["pendientes_vistos"],
        summary["protegidos"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


# ---------------------------------------------------------------------------
# Backfill del origen de los vínculos legados (AREA-07-01)
# ---------------------------------------------------------------------------


async def run_once_origen_legado() -> dict[str, int]:
    """Una barrida: por cada tenant activo, completa productora/formato/tamaño de hasta
    `indice_reconciliation_batch_size` vínculos legados de expedientes ABIERTOS. Idempotente
    (`origen_completado_at`): cada vínculo se intenta una vez."""
    pool = get_pool()
    slugs = await list_active_tenants(pool)
    total = {"vistos": 0, "completados": 0, "sin_cambios": 0}
    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:origen_legado'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    continue
                try:
                    resumen = await ArchiveService(conn, slug).completar_origen_legado(
                        settings.indice_reconciliation_batch_size
                    )
                    for k, v in resumen.items():
                        total[k] += v
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:origen_legado'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en el backfill del origen de vínculos para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )
    return total


# ---------------------------------------------------------------------------
# WORM de las firmas PERSONALES del acta (AREA-07-10)
# ---------------------------------------------------------------------------
#
# Gemela de `run_once_actas_worm` para `transferencia_acta_firma_personal_worm` (migración
# 057): lo que `firmar_personal` no pudo proteger post-commit (storage caído, retención TRD sin
# resolver) se repite con backoff. Reutiliza `proteger_worm_firma_personal`.


async def run_once_firmas_personales_worm() -> dict[str, int]:
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    summary = {"pendientes_vistos": 0, "protegidos": 0, "fallidos": 0, "agotados": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:firma_personal_worm'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    continue
                try:
                    rows = await TransferenciaRepository(conn, slug).list_firma_personal_worm_pending(
                        settings.indice_reconciliation_batch_size
                    )
                    service = TransferenciaService(conn, slug)
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        resultado = await service.proteger_worm_firma_personal(
                            row["firma_personal_id"], actor_id=None, origen="reconciliation"
                        )
                        if resultado == "protegido":
                            summary["protegidos"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:firma_personal_worm'), "
                        "hashtext($1))",
                        slug,
                    )
            # Constancias de «firma inobtenible» (migración 061): sello y firma personal del
            # declarante, mismo ciclo y mismo tipo de reintento. Lock con namespace propio.
            async with acquire_tenant(slug) as conn:
                got_lock_exc = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:firma_excepcion_worm'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock_exc:
                    continue
                try:
                    repo = TransferenciaRepository(conn, slug)
                    service = TransferenciaService(conn, slug)
                    rows = await repo.list_firma_excepcion_worm_pending(
                        settings.indice_reconciliation_batch_size
                    )
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        resultado = await service.proteger_worm_firma_excepcion(
                            row["excepcion_id"],
                            row["artefacto"],
                            actor_id=None,
                            origen="reconciliation",
                        )
                        if resultado == "protegido":
                            summary["protegidos"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                    # Revocaciones de la constancia (migración 062): mismo ciclo y mismo lock.
                    # De esta barrida depende que una constancia se pueda RE-DECLARAR: el
                    # trigger exige la revocación sellada y protegida.
                    rows_rev = await repo.list_firma_excepcion_worm_pending(
                        settings.indice_reconciliation_batch_size, acto="revocacion"
                    )
                    for row in rows_rev:
                        summary["pendientes_vistos"] += 1
                        resultado = await service.proteger_worm_revocacion(
                            row["revocacion_id"],
                            row["artefacto"],
                            actor_id=None,
                            origen="reconciliation",
                        )
                        if resultado == "protegido":
                            summary["protegidos"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                    # Sobres de revelación huérfanos (064): el sobre se subió pero la fila nunca lo
                    # referenció y la purga inmediata falló.
                    barrido = await service.purgar_sobres_huerfanos(
                        limite=settings.indice_reconciliation_batch_size
                    )
                    if barrido["purgados"]:
                        summary["sobres_huerfanos_purgados"] = (
                            summary.get("sobres_huerfanos_purgados", 0) + barrido["purgados"]
                        )
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:firma_excepcion_worm'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida WORM de las firmas personales para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )
    return summary


# ---------------------------------------------------------------------------
# Evento PREMIS del fixity-check de la recepción (AREA-07-05)
# ---------------------------------------------------------------------------
#
# `recibir()` registra el evento en storage-service post-commit y best-effort; esta fase lo
# repite con backoff cuando falló (storage caído en ese instante). Mismo ciclo/cadencia que el
# WORM del acta. Reutiliza `TransferenciaService.registrar_fixity_premis`, el MISMO método del
# disparo síncrono, así que no duplica la lógica de reintento ni la idempotencia (`dedup_key`).


async def run_once_fixity_premis() -> dict[str, int]:
    """Una barrida: por cada tenant activo, hasta `indice_reconciliation_batch_size`
    recepciones con el evento PREMIS del fixity-check sin confirmar."""
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    summary = {"pendientes_vistos": 0, "registrados": 0, "fallidos": 0, "agotados": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:fixity_premis'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    continue
                try:
                    rows = await TransferenciaRepository(conn, slug).list_fixity_premis_pending(
                        settings.indice_reconciliation_batch_size
                    )
                    service = TransferenciaService(conn, slug)
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        resultado = await service.registrar_fixity_premis(
                            row["transferencia_id"], origen="reconciliation"
                        )
                        if resultado == "registrado":
                            summary["registrados"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:fixity_premis'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida del evento PREMIS de fixity para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )
    return summary


# ---------------------------------------------------------------------------
# WORM del ACTA DE ELIMINACIÓN (AREA-06-02)
# ---------------------------------------------------------------------------
#
# Gemela de `run_once_actas_worm` para el acta del lote de eliminación
# (RF-TRD-07, fase 4). Existía el índice parcial
# `ix_eliminacion_acta_firma_worm_pendiente` (migración 050) y NINGÚN consumidor:
# el cableado era de un solo intento y un fallo del almacén dejaba el acta sin
# candado para siempre. Ahora `ejecutar_lote_eliminacion` exige el candado antes
# de destruir, así que esta barrida es lo que desbloquea la ejecución sin
# intervención manual. Mismo ciclo/cadencia que `run_once_actas_worm`.


async def _reconciliar_worm_acta_eliminacion(
    conn: asyncpg.Connection, slug: str, row: asyncpg.Record
) -> str:
    """Intenta proteger bajo WORM UN acta de eliminación 'firmado' pendiente.
    Delgado a propósito: reutiliza el MISMO método del disparo síncrono, con la
    atribución de sistema (`SYSTEM_RECONCILER_ID`)."""
    service = ArchiveService(conn, slug)
    return await service._proteger_worm_acta_eliminacion(
        row["lote_id"], actor=SYSTEM_RECONCILER_ID
    )


async def run_once_actas_eliminacion_worm() -> dict[str, int]:
    """Una barrida completa: por cada tenant activo, hasta
    `indice_reconciliation_batch_size` actas de eliminación 'firmado' sin
    proteger bajo WORM. Fallos por-tenant o por-acta quedan aislados."""
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    summary = {"pendientes_vistos": 0, "protegidos": 0, "fallidos": 0, "agotados": 0}

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock("
                    "hashtext('orpycamcp:archive:acta_eliminacion_worm_reconcile'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    logger.debug(
                        "Reconciliación WORM del acta de eliminación: tenant=%s ya está "
                        "siendo procesado por otra instancia, se omite esta barrida",
                        slug,
                    )
                    continue
                try:
                    rows = await ArchiveRepository(
                        conn, slug
                    ).list_worm_pending_actas_eliminacion(
                        settings.indice_reconciliation_batch_size
                    )
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        try:
                            resultado = await _reconciliar_worm_acta_eliminacion(
                                conn, slug, row
                            )
                        except Exception:
                            logger.warning(
                                "Fallo reconciliando WORM del acta de eliminación "
                                "lote_id=%s tenant=%s — se omite en esta barrida",
                                row["lote_id"],
                                slug,
                                exc_info=True,
                            )
                            summary["fallidos"] += 1
                            continue
                        if resultado == "protegido":
                            summary["protegidos"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock("
                        "hashtext('orpycamcp:archive:acta_eliminacion_worm_reconcile'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación WORM del acta de eliminación "
                "para tenant=%s — se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )

    logger.info(
        "Reconciliación WORM del acta de eliminación completada: "
        "pendientes_vistos=%d protegidos=%d fallidos=%d agotados=%d tenants=%d",
        summary["pendientes_vistos"],
        summary["protegidos"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


# ---------------------------------------------------------------------------
# Quinta fase — renovación de retención WORM para series de disposición
# Conservación Total (E10 debt)
# ---------------------------------------------------------------------------
#
# Objetos bajo Object-Lock COMPLIANCE (Increment A/Inc.1/Inc.2 wiring) llevan
# `retain_until = fecha_inicio + retención TOTAL de la TRD`. Para series de
# disposición Conservación Total (CT) el documento debe conservarse PARA
# SIEMPRE, pero el lock COMPLIANCE VENCE en esa fecha -- esta fase EXTIENDE
# `retain_until` (ventana rodante `now() + total_TRD`, NUNCA una fecha
# lejana/9999) antes de que expire, para el índice, el acta de transferencia
# Y (E10 debt, fast-follow del cableado del AIP) el AIP OAIS -- los TRES
# artefactos con espejo local en este servicio (`expediente_indice`/
# `transferencia_acta_firma`/`expediente_aip`, ver `app/core/disposition.py`).
#
# UNA sola función (`run_once_worm_renovacion`) cubre los TRES artefactos --
# a diferencia de las fases 3/4/6 (`run_once_worm`/`run_once_actas_worm`/
# `run_once_aip`, separadas porque cada una tenía su propio "artefacto recién
# firmado/empaquetado" que disparaba la protección inicial), la renovación es
# un barrido periódico puramente reactivo al vencimiento próximo sin un
# evento que la dispare — no hay razón para separarla en tres funciones ni
# darle cadencia propia. Piggyback en el MISMO ciclo/cadencia que la 3ra fase
# (índice, `_indice_reconciliation_loop` en `app/main.py`) — reutiliza
# `indice_reconciliation_max_intentos`/`backoff_base_seconds`/`cap_seconds`/
# `batch_size`, mismo criterio que ató las fases WORM anteriores al ciclo del
# sellado (barata: el índice parcial de las migraciones 031/032/034 devuelve
# vacío casi siempre en un tenant sin series CT próximas a vencer).
#
# Fail-closed por construcción (`CT_DISPOSITION_CODES`, ver
# `app/core/disposition.py`): el filtro CT vive EN LA CONSULTA SQL de
# `list_worm_renovacion_pending` -- una serie E/S/M jamás entra al resultado,
# sin importar cuán próxima esté a vencer (expiran y se disponen
# legítimamente, renovarlas sería incorrecto).


async def _reconciliar_worm_renovacion_indice(
    conn: asyncpg.Connection, slug: str, row: asyncpg.Record
) -> str:
    """Intenta renovar la retención WORM de UN índice CT próximo a vencer.
    Devuelve 'renovado', 'omitido' (llamada confirmada pero sin extensión
    real -- no-op idempotente), 'pendiente'/'fallido' (backoff programado) o
    'agotado' (`worm_renovacion_agotado=TRUE`, requiere intervención manual
    URGENTE — riesgo de vencimiento del lock COMPLIANCE sobre un registro de
    conservación permanente)."""
    index = IndexService(conn, slug)
    return await index.renovar_worm_indice(
        row["expediente_id"],
        row["id"],
        intentos_previos=int(row["worm_renovacion_intentos"]),
    )


async def _reconciliar_worm_renovacion_acta(
    conn: asyncpg.Connection, slug: str, row: asyncpg.Record
) -> str:
    """Intenta renovar la retención WORM de UNA acta CT próxima a vencer --
    gemela EXACTA de `_reconciliar_worm_renovacion_indice`."""
    service = TransferenciaService(conn, slug)
    return await service.renovar_worm_acta(
        row["transferencia_id"],
        row["expediente_id"],
        intentos_previos=int(row["worm_renovacion_intentos"]),
    )


async def _reconciliar_worm_renovacion_firma_personal(
    conn: asyncpg.Connection, slug: str, row: asyncpg.Record
) -> str:
    """Renueva la retención WORM del XML de UNA firma personal del acta (E10 debt,
    AREA-07-10) -- gemela de `_reconciliar_worm_renovacion_acta`."""
    service = TransferenciaService(conn, slug)
    return await service.renovar_worm_firma_personal(
        row["firma_personal_id"],
        row["transferencia_id"],
        row["expediente_id"],
        row["rol"],
        intentos_previos=int(row["worm_renovacion_intentos"]),
    )


async def _reconciliar_worm_renovacion_aip(
    conn: asyncpg.Connection, slug: str, row: asyncpg.Record
) -> str:
    """Intenta renovar la retención WORM del AIP de UN expediente CT próximo
    a vencer (E10 debt, fast-follow del cableado del AIP) -- gemela EXACTA de
    `_reconciliar_worm_renovacion_indice`."""
    service = AipWiringService(conn, slug)
    return await service.renovar_worm_aip(
        row["expediente_id"],
        intentos_previos=int(row["worm_renovacion_intentos"]),
    )


async def run_once_worm_renovacion() -> dict[str, int]:
    """Una barrida completa: por cada tenant activo, renueva hasta
    `indice_reconciliation_batch_size` índices Y hasta ese mismo cupo de
    actas de series CT próximas a vencer (`worm_renovacion_ventana_dias`).
    Fallos por-tenant o por-artefacto quedan aislados (WARNING, no abortan el
    resto de la barrida) — mismo patrón que `run_once_worm`/
    `run_once_actas_worm`.

    CUATRO advisory locks POR TENANT en esta barrida (`worm_renovacion_indice`/
    `worm_renovacion_acta`/`worm_renovacion_aip`/`worm_renovacion_firma_personal`,
    namespaces propios) --
    índice, acta y AIP son artefactos independientes, no deben serializarse
    entre sí; cada uno SÍ se serializa contra otra instancia de ESTA MISMA
    fase vía su propio lock (mismo criterio que las fases 3/4/6)."""
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    summary = {"pendientes_vistos": 0, "renovados": 0, "fallidos": 0, "agotados": 0}
    ct_codes = list(CT_DISPOSITION_CODES)

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock("
                    "hashtext('orpycamcp:archive:worm_renovacion_indice'), hashtext($1))",
                    slug,
                )
                if got_lock:
                    try:
                        rows = await IndexRepository(conn).list_worm_renovacion_pending(
                            ventana_dias=settings.worm_renovacion_ventana_dias,
                            ct_codes=ct_codes,
                            limit=settings.indice_reconciliation_batch_size,
                        )
                        for row in rows:
                            summary["pendientes_vistos"] += 1
                            try:
                                resultado = await _reconciliar_worm_renovacion_indice(
                                    conn, slug, row
                                )
                            except Exception:
                                logger.warning(
                                    "Fallo renovando retención WORM indice_id=%s "
                                    "(expediente_id=%s) tenant=%s — se omite en esta barrida",
                                    row["id"],
                                    row["expediente_id"],
                                    slug,
                                    exc_info=True,
                                )
                                summary["fallidos"] += 1
                                continue
                            if resultado == "renovado":
                                summary["renovados"] += 1
                            elif resultado == "agotado":
                                summary["agotados"] += 1
                            else:
                                summary["fallidos"] += 1
                    finally:
                        await conn.execute(
                            "SELECT pg_advisory_unlock("
                            "hashtext('orpycamcp:archive:worm_renovacion_indice'), "
                            "hashtext($1))",
                            slug,
                        )
                else:
                    logger.debug(
                        "Renovación WORM del índice: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )

                got_lock_acta = await conn.fetchval(
                    "SELECT pg_try_advisory_lock("
                    "hashtext('orpycamcp:archive:worm_renovacion_acta'), hashtext($1))",
                    slug,
                )
                if got_lock_acta:
                    try:
                        rows = await TransferenciaRepository(
                            conn, slug
                        ).list_worm_renovacion_pending(
                            ventana_dias=settings.worm_renovacion_ventana_dias,
                            ct_codes=ct_codes,
                            limit=settings.indice_reconciliation_batch_size,
                        )
                        for row in rows:
                            summary["pendientes_vistos"] += 1
                            try:
                                resultado = await _reconciliar_worm_renovacion_acta(
                                    conn, slug, row
                                )
                            except Exception:
                                logger.warning(
                                    "Fallo renovando retención WORM transferencia_id=%s "
                                    "(expediente_id=%s) tenant=%s — se omite en esta barrida",
                                    row["transferencia_id"],
                                    row["expediente_id"],
                                    slug,
                                    exc_info=True,
                                )
                                summary["fallidos"] += 1
                                continue
                            if resultado == "renovado":
                                summary["renovados"] += 1
                            elif resultado == "agotado":
                                summary["agotados"] += 1
                            else:
                                summary["fallidos"] += 1
                    finally:
                        await conn.execute(
                            "SELECT pg_advisory_unlock("
                            "hashtext('orpycamcp:archive:worm_renovacion_acta'), "
                            "hashtext($1))",
                            slug,
                        )
                else:
                    logger.debug(
                        "Renovación WORM del acta: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )

                got_lock_aip = await conn.fetchval(
                    "SELECT pg_try_advisory_lock("
                    "hashtext('orpycamcp:archive:worm_renovacion_aip'), hashtext($1))",
                    slug,
                )
                if got_lock_aip:
                    try:
                        rows = await AipRepository(conn).list_worm_renovacion_pending(
                            ventana_dias=settings.worm_renovacion_ventana_dias,
                            ct_codes=ct_codes,
                            limit=settings.indice_reconciliation_batch_size,
                        )
                        for row in rows:
                            summary["pendientes_vistos"] += 1
                            try:
                                resultado = await _reconciliar_worm_renovacion_aip(
                                    conn, slug, row
                                )
                            except Exception:
                                logger.warning(
                                    "Fallo renovando retención WORM del AIP "
                                    "expediente_id=%s tenant=%s — se omite en esta barrida",
                                    row["expediente_id"],
                                    slug,
                                    exc_info=True,
                                )
                                summary["fallidos"] += 1
                                continue
                            if resultado == "renovado":
                                summary["renovados"] += 1
                            elif resultado == "agotado":
                                summary["agotados"] += 1
                            else:
                                summary["fallidos"] += 1
                    finally:
                        await conn.execute(
                            "SELECT pg_advisory_unlock("
                            "hashtext('orpycamcp:archive:worm_renovacion_aip'), "
                            "hashtext($1))",
                            slug,
                        )
                else:
                    logger.debug(
                        "Renovación WORM del AIP: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )

                # Firmas PERSONALES del acta (E10 debt, AREA-07-10): lock con namespace propio,
                # independiente del del sello institucional (artefactos distintos).
                got_lock_firma_personal = await conn.fetchval(
                    "SELECT pg_try_advisory_lock("
                    "hashtext('orpycamcp:archive:worm_renovacion_firma_personal'), "
                    "hashtext($1))",
                    slug,
                )
                if got_lock_firma_personal:
                    try:
                        rows = await TransferenciaRepository(
                            conn, slug
                        ).list_firma_personal_renovacion_pending(
                            ventana_dias=settings.worm_renovacion_ventana_dias,
                            ct_codes=ct_codes,
                            limit=settings.indice_reconciliation_batch_size,
                        )
                        for row in rows:
                            summary["pendientes_vistos"] += 1
                            try:
                                resultado = await _reconciliar_worm_renovacion_firma_personal(
                                    conn, slug, row
                                )
                            except Exception:
                                logger.warning(
                                    "Fallo renovando retención WORM firma_personal_id=%s "
                                    "(expediente_id=%s) tenant=%s — se omite en esta barrida",
                                    row["firma_personal_id"],
                                    row["expediente_id"],
                                    slug,
                                    exc_info=True,
                                )
                                summary["fallidos"] += 1
                                continue
                            if resultado == "renovado":
                                summary["renovados"] += 1
                            elif resultado == "agotado":
                                summary["agotados"] += 1
                            else:
                                summary["fallidos"] += 1
                    finally:
                        await conn.execute(
                            "SELECT pg_advisory_unlock("
                            "hashtext('orpycamcp:archive:worm_renovacion_firma_personal'), "
                            "hashtext($1))",
                            slug,
                        )
                else:
                    logger.debug(
                        "Renovación WORM de firmas personales: tenant=%s ya está siendo "
                        "procesado por otra instancia, se omite esta barrida",
                        slug,
                    )
                # Constancias de «firma inobtenible» (migración 061): lock propio; sello y
                # firma personal del declarante, un `tipo` de storage por rol y artefacto.
                got_lock_exc = await conn.fetchval(
                    "SELECT pg_try_advisory_lock("
                    "hashtext('orpycamcp:archive:worm_renovacion_firma_excepcion'), "
                    "hashtext($1))",
                    slug,
                )
                if got_lock_exc:
                    try:
                        repo_exc = TransferenciaRepository(conn, slug)
                        # Constancia y revocación comparten ciclo: se barren las dos listas con
                        # el mismo código, cambiando solo la clave de la fila y el método.
                        for acto, clave, renovar in (
                            ("excepcion", "excepcion_id", "renovar_worm_firma_excepcion"),
                            ("revocacion", "revocacion_id", "renovar_worm_revocacion"),
                        ):
                            rows = await repo_exc.list_firma_excepcion_renovacion_pending(
                                ventana_dias=settings.worm_renovacion_ventana_dias,
                                ct_codes=ct_codes,
                                limit=settings.indice_reconciliation_batch_size,
                                acto=acto,
                            )
                            for row in rows:
                                summary["pendientes_vistos"] += 1
                                try:
                                    resultado = await getattr(
                                        TransferenciaService(conn, slug), renovar
                                    )(
                                        row[clave],
                                        row["artefacto"],
                                        row["transferencia_id"],
                                        row["expediente_id"],
                                        row["rol"],
                                        intentos_previos=int(row["worm_renovacion_intentos"]),
                                        minio_key_firmado=row["minio_key_firmado"],
                                    )
                                except Exception:
                                    logger.warning(
                                        "Fallo renovando retención WORM %s=%s artefacto=%s "
                                        "(expediente_id=%s) tenant=%s — se omite en esta barrida",
                                        clave,
                                        row[clave],
                                        row["artefacto"],
                                        row["expediente_id"],
                                        slug,
                                        exc_info=True,
                                    )
                                    summary["fallidos"] += 1
                                    continue
                                if resultado == "renovado":
                                    summary["renovados"] += 1
                                elif resultado == "agotado":
                                    summary["agotados"] += 1
                                else:
                                    summary["fallidos"] += 1
                    finally:
                        await conn.execute(
                            "SELECT pg_advisory_unlock("
                            "hashtext('orpycamcp:archive:worm_renovacion_firma_excepcion'), "
                            "hashtext($1))",
                            slug,
                        )
        except Exception:
            logger.warning(
                "Fallo en la barrida de renovación de retención WORM para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )

    logger.info(
        "Renovación de retención WORM (series CT) completada: "
        "pendientes_vistos=%d renovados=%d fallidos=%d agotados=%d tenants=%d",
        summary["pendientes_vistos"],
        summary["renovados"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


# ---------------------------------------------------------------------------
# Sexta fase — cableado del AIP OAIS al cierre del expediente (E10 debt "AIP
# sin-wiring")
# ---------------------------------------------------------------------------
#
# Converge los expedientes `closed`/`transferred`, con índice electrónico
# VIGENTE `estado='firmado'` y `worm_protegido_at IS NOT NULL` (E10 Inc.1),
# cuyo AIP OAIS (`POST /preservacion/aip[-internal]`, storage-service
# Increment B) todavía no se empaquetó — RECONCILIACIÓN-ONLY (diseño
# ratificado por `orfeo-architect`): a diferencia del sellado/cableado WORM
# del índice (fases 1/3, baratas: firman/protegen un XML ya en `files`), el
# AIP DESCARGA los bytes reales de cada anexo digital + arma un ZIP
# server-side — demasiado caro para intentarse síncronamente en el path de
# la petición de cierre del expediente. Delgada sobre `AipWiringService.
# intentar_empaquetar` (misma resolución cross-servicio de `file_id` vía
# document-service, misma derivación de retención TRD, misma marca
# atómica+asiento) para no duplicar esa lógica aquí — `_reconciliar_aip` solo
# interpreta el resultado para el `summary` del barrido, mismo rol que
# `_reconciliar_worm`.
#
# Piggyback en el MISMO ciclo/cadencia que la 3ra fase (índice,
# `_indice_reconciliation_loop` en `app/main.py`) — mismo criterio que ató
# `run_once_worm_renovacion` a ese ciclo: tan estrechamente acoplada a "el
# índice ya quedó firmado+protegido" que no amerita kill-switch/intervalo
# propio, reutiliza `indice_reconciliation_batch_size`/`_max_intentos`/
# `_backoff_base_seconds`/`_backoff_cap_seconds`.


async def _reconciliar_aip(conn: asyncpg.Connection, slug: str, row: asyncpg.Record) -> str:
    """Intenta empaquetar el AIP de UN expediente elegible. Devuelve
    'empaquetado', 'omitido' (expediente 100% físico, TERMINAL),
    'fallido'/'pendiente' (backoff programado) o 'agotado'
    (`expediente_aip.estado='agotado'`, requiere intervención manual — NUNCA
    se inventa conformidad)."""
    service = AipWiringService(conn, slug)
    return await service.intentar_empaquetar(row["expediente_id"])


async def run_once_aip() -> dict[str, int]:
    """Una barrida completa: por cada tenant activo, hasta
    `indice_reconciliation_batch_size` expedientes elegibles para empaquetar
    el AIP. Fallos por-tenant o por-expediente quedan aislados (WARNING, no
    abortan el resto de la barrida) — mismo patrón que `run_once_worm`."""
    pool = get_pool()
    # Solo las ACTIVAS, a propósito (B10 R2-2): empaquetar el AIP necesita los anexos del
    # expediente, que document-service entrega por `fetch_anexos_internal` y VETA para una
    # entidad suspendida. Visitarla solo agotaría reintentos (`archive.aip_agotado`, terminal
    # hasta un reintento manual). El empaquetado ESPERA a la reactivación; lo que no puede
    # esperar es la retención del AIP ya existente (`run_once_worm_renovacion`), que sí recorre
    # las suspendidas.
    slugs = await list_active_tenants(pool)
    summary = {
        "pendientes_vistos": 0,
        "empaquetados": 0,
        "omitidos": 0,
        "fallidos": 0,
        "agotados": 0,
    }

    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                # Namespace de advisory lock PROPIO (`aip_reconcile`, distinto
                # de `indice_reconcile`/`acta_reconcile`/`worm_reconcile`/
                # `acta_worm_reconcile`/`worm_renovacion_*`) — esta fase puede
                # correr en el mismo ciclo que las demás sin competir por el
                # mismo lock. También reduce la ventana de doble-empaquetado
                # del lado de storage-service (ver migración 012 de ese
                # servicio): dos instancias de ESTE job para el mismo tenant
                # nunca corren `run_once_aip` en paralelo.
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:aip_reconcile'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    logger.debug(
                        "Cableado AIP: tenant=%s ya está siendo procesado "
                        "por otra instancia, se omite esta barrida",
                        slug,
                    )
                    continue
                try:
                    rows = await AipRepository(conn).list_pending(
                        settings.indice_reconciliation_batch_size
                    )
                    for row in rows:
                        summary["pendientes_vistos"] += 1
                        try:
                            resultado = await _reconciliar_aip(conn, slug, row)
                        except Exception:
                            logger.warning(
                                "Fallo empaquetando AIP expediente_id=%s tenant=%s "
                                "— se omite este expediente en esta barrida",
                                row["expediente_id"],
                                slug,
                                exc_info=True,
                            )
                            summary["fallidos"] += 1
                            continue
                        if resultado == "empaquetado":
                            summary["empaquetados"] += 1
                        elif resultado == "omitido":
                            summary["omitidos"] += 1
                        elif resultado == "agotado":
                            summary["agotados"] += 1
                        else:
                            summary["fallidos"] += 1
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:aip_reconcile'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de cableado AIP para tenant=%s "
                "— se omite este tenant en esta barrida",
                slug,
                exc_info=True,
            )

    logger.info(
        "Cableado del AIP completado: "
        "pendientes_vistos=%d empaquetados=%d omitidos=%d fallidos=%d agotados=%d tenants=%d",
        summary["pendientes_vistos"],
        summary["empaquetados"],
        summary["omitidos"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


async def run_once_aip_reempaquetado() -> dict[str, int]:
    """Fase 11 (C-6, ADR-042): reempaqueta los AIP cuyo índice embebido ya no es el vigente.

    Barrido de CONCILIACIÓN contra `firma_artefacto_vigente` de signature-service (no consume
    `firma.lta_renovada`: ese evento es best-effort y sin outbox). Recorre entidades `active` y
    `suspended` -- reempaquetar es CONSERVACIÓN (el plazo del sello corre aunque la entidad esté
    suspendida, ADR-035 §8) y storage reconstruye desde su propio bag, sin tocar document-service.
    Cadencia propia: `aip_reempaquetado_intervalo_s` (`0` apaga la fase)."""
    summary = {"revisados": 0, "reempaquetados": 0, "sin_cambio": 0, "fallidos": 0, "agotados": 0}
    if settings.aip_reempaquetado_intervalo_s <= 0:
        return summary
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:aip_reempaquetado'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    continue
                try:
                    parcial = await AipWiringService(conn, slug).barrer_reempaquetado(
                        settings.indice_reconciliation_batch_size
                    )
                    for k, v in parcial.items():
                        summary[k] += v
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:aip_reempaquetado'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de reempaquetado del AIP para tenant=%s", slug, exc_info=True
            )
    logger.info(
        "Reempaquetado del AIP completado: revisados=%d reempaquetados=%d sin_cambio=%d "
        "fallidos=%d agotados=%d tenants=%d",
        summary["revisados"],
        summary["reempaquetados"],
        summary["sin_cambio"],
        summary["fallidos"],
        summary["agotados"],
        len(slugs),
    )
    return summary


async def run_once_valoracion_holds() -> dict[str, int]:
    """Fase 12 (C-5 / C7, ADR-043): aplica la valoración de la TVD a los expedientes de fondo
    acumulado cuyo hold `valoracion_pendiente` ya tiene valoración registrada y aún no se aplicó.

    Orden conservador por expediente (ver `ValoracionService.aplicar_expediente`): CT fija la
    retención y DESPUÉS levanta el hold; E/S/M solo levanta. Espera (sin consumir intentos) mientras
    quede un artefacto de contenido por proteger. Con backoff y `agotada` al agotar los intentos.
    Recorre entidades `active` y `suspended`: levantar un hold es CONSERVACIÓN (ADR-035 §8), no
    operación."""
    pool = get_pool()
    slugs = await list_tenants_conservacion(pool)
    summary = {"vistos": 0, "aplicadas": 0, "esperando": 0, "pendientes": 0, "agotadas": 0}
    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext('orpycamcp:archive:valoracion_holds'), "
                    "hashtext($1))",
                    slug,
                )
                if not got_lock:
                    continue
                try:
                    parcial = await ValoracionService(conn, slug).barrer(
                        settings.valoracion_hold_batch_size
                    )
                    for k, v in parcial.items():
                        summary[k] += v
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext('orpycamcp:archive:valoracion_holds'), "
                        "hashtext($1))",
                        slug,
                    )
        except Exception:
            logger.warning(
                "Fallo en la barrida de valoración de holds para tenant=%s", slug, exc_info=True
            )
    logger.info(
        "Valoración de holds completada: vistos=%d aplicadas=%d esperando=%d pendientes=%d "
        "agotadas=%d tenants=%d",
        summary["vistos"],
        summary["aplicadas"],
        summary["esperando"],
        summary["pendientes"],
        summary["agotadas"],
        len(slugs),
    )
    return summary


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    await create_pool(settings.database_url_asyncpg)
    try:
        await run_once()
        await run_once_actas()
        await run_once_worm()
        await run_once_actas_worm()
        await run_once_actas_eliminacion_worm()
        await run_once_worm_renovacion()
        await run_once_aip()
        await run_once_aip_reempaquetado()
        await run_once_valoracion_holds()
        await run_once_origen_legado()
        await run_once_firmas_personales_worm()
        await run_once_fixity_premis()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
