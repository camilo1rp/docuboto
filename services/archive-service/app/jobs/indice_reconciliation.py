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
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import acquire_tenant, close_pool, create_pool, get_pool
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.backoff import next_backoff_seconds
from app.services.index_service import IndexService

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
    outcome = await signature_client.sign_indice_internal(
        tenant_slug=slug,
        payload_xml=row["xml"],
        objeto_id=str(row["expediente_id"]),
        indice_id=str(row["id"]),
        version=row["version"],
    )

    if outcome.signed is not None:
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
        return "agotado"

    await repo.bump_intento(row["id"], proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado)
    return "fallido"


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
    slugs = await list_active_tenants(pool)
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
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
