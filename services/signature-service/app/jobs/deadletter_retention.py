# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retention job for evento_dead_letter (ADR-021 Fase 4 — MVP-3, signature-service).

Purges terminal rows (status IN ('resolved','discarded')) that exceed the configured
grace period (default 90 days after resolved_at).  'pending' rows are NEVER deleted:
they represent unresolved failures awaiting human decision.

The purge is OPERATIONAL — no archival act required.  The legal copy of every
dead-letter event lives in audit_log (immutable, written atomically at insertion time
by _persist_dead_letter).  A structured log entry records the per-tenant row counts.

Classification rationale (archival-compliance-auditor review):
  - evento_dead_letter is operational infrastructure (dead-letter queue metadata),
    not a documental series under Ley 594/2000 art. 23.
  - Each row's resolution/discard was already recorded in audit_log (Fase 5 replay/discard
    endpoints write audit entries before the status transition).  Deleting the row does
    not remove the legal trace — it removes the operational metadata that is no longer
    needed once the event has been handled.
  - Purging only terminal states (resolved/discarded) guarantees that unresolved
    failures are never silently deleted.

Execution modes:
  1. Embedded in the stream worker: ``run_once()`` is called on a daily cadence
     gated by ``time.monotonic()`` inside ``document_consumer.run()``.
  2. Standalone (cron / manual): ``python -m app.jobs.deadletter_retention``
     initialises the pool, purges all active tenants, then shuts down cleanly.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.core.config import settings
from app.core.constants import DLQ_GROUP
from app.core.database import acquire_tenant, close_pool, create_pool, get_pool
from app.repositories.dead_letter_repository import DeadLetterRepository

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


async def purge_deadletter(days: int) -> dict[str, int]:
    """Purge terminal dead-letter rows older than `days` days for every active tenant.

    Iterates active tenants from public.tenants; each purge runs in its own
    connection with the tenant search_path set by ``acquire_tenant``.

    A failure in one tenant is logged as WARNING and does NOT abort the rest —
    the next daily run will retry the failed tenant.  The returned dict maps
    slug → deleted-row-count for tenants that completed successfully.

    Only 'resolved' and 'discarded' rows are deleted.  'pending' rows are never
    touched regardless of age (they require human decision before any disposition).

    `evento_dead_letter` is shared with workflow-service in the same tenant schema
    (see the module note in `dead_letter_repository.py`): every call passes
    `DLQ_GROUP` (this service's own constant) so the purge never touches rows
    that belong to the other consumer's retention window.
    """
    pool = get_pool()
    slugs = await list_active_tenants(pool)
    totals: dict[str, int] = {}
    for slug in slugs:
        try:
            async with acquire_tenant(slug) as conn:
                n = await DeadLetterRepository(conn).purge_terminal(
                    days, slug, DLQ_GROUP
                )
            totals[slug] = n
            logger.debug(
                "Retención dead-letter tenant=%s: %d filas eliminadas", slug, n
            )
        except Exception:
            logger.warning(
                "Fallo purgando dead-letters para tenant=%s — se omite este tenant",
                slug,
                exc_info=True,
            )
    total_rows = sum(totals.values())
    logger.info(
        "Retención dead-letter completada: %d filas totales eliminadas en %d/%d tenants "
        "(gracia=%d días, solo estados terminales resolved/discarded)",
        total_rows,
        len(totals),
        len(slugs),
        days,
    )
    return totals


async def run_once() -> None:
    """Run a single retention sweep using the configured grace period.

    Called by the stream worker on its daily cadence and by the standalone
    entry point.  Delegates to ``purge_deadletter``; errors propagate to the
    caller (the worker wraps this in its own try/except so the loop survives
    failures).
    """
    await purge_deadletter(settings.retention_deadletter_days)


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
