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
    el DLQ)."""
    base = settings.indice_reconciliation_backoff_base_seconds
    cap = settings.indice_reconciliation_backoff_cap_seconds
    # Acota el exponente (no el resultado) para no desbordar un int nativo
    # con un `reconcile_intentos` anómalamente alto — `min(cap, ...)` ya
    # acota el resultado final, esto es defensa adicional sobre el propio
    # cálculo de la potencia.
    return min(cap, base * (2 ** min(intentos_tras_este_fallo, 20)))


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
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
