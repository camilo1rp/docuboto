# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests for retention job (ADR-021 Fase 4 — MVP-1).

All tests are unit-level (no real DB / pool required): asyncpg
connections, the pool, and acquire_tenant are mocked.

Coverage:
  1. purge_older_than — deletes old rows, returns parsed row count.
  2. purge_older_than — preserves rows within TTL (DELETE 0).
  3. list_active_tenants — filters by status + provisioning_status.
  4. purge_dedup — aggregates counts across multiple tenants.
  5. purge_dedup — a failure in one tenant does NOT abort the rest.

The worker's daily call (and its failure isolation) is covered in
test_stream_worker.py (`_mantenimiento_diario`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAcquire:
    """Minimal async context manager that yields a mock asyncpg connection."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


def _make_pool_with_tenants(slugs: list[str]) -> MagicMock:
    """Build a minimal asyncpg pool mock whose acquire() yields a conn that
    returns *slugs* rows from public.tenants."""
    conn = AsyncMock()
    conn.fetch.return_value = [{"slug": s} for s in slugs]
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


# ---------------------------------------------------------------------------
# 1. purge_older_than — rows deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_older_than_returns_deleted_count() -> None:
    """purge_older_than parses 'DELETE N' from conn.execute and returns N."""
    from notification.repositories.processed_events_repository import ProcessedEventsRepository

    conn = AsyncMock()
    conn.execute.return_value = "DELETE 5"
    repo = ProcessedEventsRepository(conn)

    count = await repo.purge_older_than(7)

    assert count == 5
    conn.execute.assert_called_once()
    sql, days_arg = conn.execute.call_args.args
    # Confirm the SQL uses make_interval (parametrised, no string interpolation).
    assert "make_interval" in sql
    assert "$1" in sql
    assert days_arg == 7


# ---------------------------------------------------------------------------
# 2. purge_older_than — nothing to delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_older_than_returns_zero_when_nothing_deleted() -> None:
    from notification.repositories.processed_events_repository import ProcessedEventsRepository

    conn = AsyncMock()
    conn.execute.return_value = "DELETE 0"
    repo = ProcessedEventsRepository(conn)

    count = await repo.purge_older_than(7)

    assert count == 0


# ---------------------------------------------------------------------------
# 3. list_active_tenants — filters by status and provisioning_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_tenants_returns_only_active() -> None:
    from notification.jobs.retention import list_active_tenants

    pool = _make_pool_with_tenants(["tenant_a", "tenant_b"])

    slugs = await list_active_tenants(pool)

    assert slugs == ["tenant_a", "tenant_b"]
    # ADR-047 §3: no lifecycle states to filter on.
    inner_conn = pool.acquire.return_value.__aenter__.return_value
    sql: str = inner_conn.fetch.call_args.args[0]
    assert "status" not in sql
    assert "public.tenants" in sql


# ---------------------------------------------------------------------------
# 4. purge_dedup — aggregates across multiple tenants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_dedup_aggregates_counts() -> None:
    from notification.jobs import retention

    conn_a = AsyncMock()
    conn_a.execute.return_value = "DELETE 3"
    conn_b = AsyncMock()
    conn_b.execute.return_value = "DELETE 7"

    fake_pool = _make_pool_with_tenants(["alpha", "beta"])

    def _fake_acquire(slug: str) -> _FakeAcquire:
        return _FakeAcquire(conn_a if slug == "alpha" else conn_b)

    with (
        patch("notification.jobs.retention.get_pool", return_value=fake_pool),
        patch("notification.jobs.retention.acquire_tenant", side_effect=_fake_acquire),
    ):
        totals = await retention.purge_dedup(days=7)

    assert totals == {"alpha": 3, "beta": 7}


# ---------------------------------------------------------------------------
# 5. purge_dedup — a single tenant failure does not abort the rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_dedup_tenant_failure_does_not_abort() -> None:
    from notification.jobs import retention

    conn_ok = AsyncMock()
    conn_ok.execute.return_value = "DELETE 4"

    fake_pool = _make_pool_with_tenants(["good", "bad", "also_good"])

    def _fake_acquire(slug: str) -> _FakeAcquire:
        if slug == "bad":
            raise RuntimeError("DB connection refused")
        return _FakeAcquire(conn_ok)

    with (
        patch("notification.jobs.retention.get_pool", return_value=fake_pool),
        patch("notification.jobs.retention.acquire_tenant", side_effect=_fake_acquire),
    ):
        totals = await retention.purge_dedup(days=7)

    # "bad" tenant is absent from totals (failed silently); good tenants completed.
    assert "bad" not in totals
    assert totals.get("good") == 4
    assert totals.get("also_good") == 4
