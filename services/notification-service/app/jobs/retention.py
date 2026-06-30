# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retention job for notification_processed_events (ADR-021 Fase 4 — MVP-1).

Purges deduplication rows that exceed the configured TTL (default 7 days).
The purge is OPERATIONAL — no archival act required; a structured log entry
records the per-tenant row counts.

Execution modes:
  1. Embedded in the stream worker: ``run_once()`` is called on a daily cadence
     gated by ``time.monotonic()`` inside ``stream_worker.run()``.
  2. Standalone (cron / manual): ``python -m app.jobs.retention`` initialises
     the pool, purges all active tenants, then shuts down cleanly.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.core.config import settings
from app.core.database import acquire_tenant, close_pool, get_pool, init_pool
from app.repositories.processed_events_repository import ProcessedEventsRepository

logger = logging.getLogger(__name__)


async def list_active_tenants(pool: asyncpg.Pool) -> list[str]:
    """Return the slugs of all tenants with status='active' and provisioning_status='active'.

    Column names verified against tenant-service migrations:
    - 001_create_tenants.sql   → slug, status
    - 002_provisioning_status.sql → provisioning_status
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug FROM public.tenants "
            "WHERE status = 'active' AND provisioning_status = 'active'"
        )
    return [row["slug"] for row in rows]


async def purge_dedup(days: int) -> dict[str, int]:
    """Purge notification_processed_events older than `days` days for every active tenant.

    Iterates active tenants from public.tenants; each purge runs in its own
    connection with the tenant search_path set by ``acquire_tenant``.

    A failure in one tenant is logged as WARNING and does NOT abort the rest —
    the next daily run will retry the failed tenant.  The returned dict maps
    slug → deleted-row-count for tenants that completed successfully.
    """
    pool = get_pool()
    slugs = await list_active_tenants(pool)
    totals: dict[str, int] = {}
    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                n = await ProcessedEventsRepository(conn).purge_older_than(days)
            totals[slug] = n
            logger.debug("Retención dedup tenant=%s: %d filas eliminadas", slug, n)
        except Exception:
            logger.warning(
                "Fallo purgando dedup para tenant=%s — se omite este tenant", slug, exc_info=True
            )
    total_rows = sum(totals.values())
    logger.info(
        "Retención dedup completada: %d filas totales eliminadas en %d/%d tenants (TTL=%d días)",
        total_rows,
        len(totals),
        len(slugs),
        days,
    )
    return totals


async def run_once() -> None:
    """Run a single retention sweep using the configured TTL.

    Called by the stream worker on its daily cadence and by the standalone
    entry point.  Delegates to ``purge_dedup``; errors propagate to the caller
    (the worker wraps this in its own try/except so the loop survives failures).
    """
    await purge_dedup(settings.retention_dedup_days)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    await init_pool(settings.database_url_asyncpg)
    try:
        await run_once()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
