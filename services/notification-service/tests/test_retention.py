# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests for retention job (ADR-021 Fase 4 — MVP-1).

All tests are unit-level (no real DB / Redis / pool required): asyncpg
connections, the pool, and acquire_tenant are mocked.

Coverage:
  1. purge_older_than — deletes old rows, returns parsed row count.
  2. purge_older_than — preserves rows within TTL (DELETE 0).
  3. list_active_tenants — filters by status + provisioning_status.
  4. purge_dedup — aggregates counts across multiple tenants.
  5. purge_dedup — a failure in one tenant does NOT abort the rest.
  6. Worker retention gate — run_once called after RETENTION_INTERVAL.
  7. Worker retention gate — run_once NOT called before RETENTION_INTERVAL.
  8. Worker retention gate — failure does NOT crash the loop.
"""

from __future__ import annotations

import asyncio
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
    from app.repositories.processed_events_repository import ProcessedEventsRepository

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
    from app.repositories.processed_events_repository import ProcessedEventsRepository

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
    from app.jobs.retention import list_active_tenants

    pool = _make_pool_with_tenants(["tenant_a", "tenant_b"])

    slugs = await list_active_tenants(pool)

    assert slugs == ["tenant_a", "tenant_b"]
    # Verify the SQL filters on both columns.
    inner_conn = pool.acquire.return_value.__aenter__.return_value
    sql: str = inner_conn.fetch.call_args.args[0]
    assert "status = 'active'" in sql
    assert "provisioning_status = 'active'" in sql
    assert "public.tenants" in sql


# ---------------------------------------------------------------------------
# 4. purge_dedup — aggregates across multiple tenants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_dedup_aggregates_counts() -> None:
    from app.jobs import retention

    conn_a = AsyncMock()
    conn_a.execute.return_value = "DELETE 3"
    conn_b = AsyncMock()
    conn_b.execute.return_value = "DELETE 7"

    fake_pool = _make_pool_with_tenants(["alpha", "beta"])

    def _fake_acquire(slug: str) -> _FakeAcquire:
        return _FakeAcquire(conn_a if slug == "alpha" else conn_b)

    with (
        patch("app.jobs.retention.get_pool", return_value=fake_pool),
        patch("app.jobs.retention.acquire_tenant", side_effect=_fake_acquire),
    ):
        totals = await retention.purge_dedup(days=7)

    assert totals == {"alpha": 3, "beta": 7}


# ---------------------------------------------------------------------------
# 5. purge_dedup — a single tenant failure does not abort the rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_dedup_tenant_failure_does_not_abort() -> None:
    from app.jobs import retention

    conn_ok = AsyncMock()
    conn_ok.execute.return_value = "DELETE 4"

    fake_pool = _make_pool_with_tenants(["good", "bad", "also_good"])

    def _fake_acquire(slug: str) -> _FakeAcquire:
        if slug == "bad":
            raise RuntimeError("DB connection refused")
        return _FakeAcquire(conn_ok)

    with (
        patch("app.jobs.retention.get_pool", return_value=fake_pool),
        patch("app.jobs.retention.acquire_tenant", side_effect=_fake_acquire),
    ):
        totals = await retention.purge_dedup(days=7)

    # "bad" tenant is absent from totals (failed silently); good tenants completed.
    assert "bad" not in totals
    assert totals.get("good") == 4
    assert totals.get("also_good") == 4


# ---------------------------------------------------------------------------
# 6. Worker gate — retention fires after RETENTION_INTERVAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_retention_gate_fires_after_interval() -> None:
    """run_once is called when now - _last_retention >= RETENTION_INTERVAL."""
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],  # first iteration — no messages
        asyncio.CancelledError(),
    ]

    mock_reclaim = AsyncMock()
    mock_run_once = AsyncMock()
    mock_time = MagicMock()
    # Call 1 (before loop): _last_reclaim = _last_retention = 0.0
    # Call 2 (inside loop): now = RETENTION_INTERVAL + 1.0  → both reclaim and retention fire
    mock_time.monotonic.side_effect = [0.0, stream_worker.RETENTION_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", mock_reclaim),
        patch("app.workers.stream_worker.retention.run_once", mock_run_once),
        patch("app.workers.stream_worker.time", mock_time),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    mock_run_once.assert_called_once()


# ---------------------------------------------------------------------------
# 7. Worker gate — retention does NOT fire before RETENTION_INTERVAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_retention_gate_does_not_fire_before_interval() -> None:
    """run_once is NOT called when the interval has not elapsed."""
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_run_once = AsyncMock()
    mock_time = MagicMock()
    # now = 0.0 always — neither reclaim (30 s) nor retention (86400 s) fires.
    mock_time.monotonic.return_value = 0.0

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.retention.run_once", mock_run_once),
        patch("app.workers.stream_worker.time", mock_time),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    mock_run_once.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Worker gate — retention failure does NOT crash the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_retention_failure_does_not_crash_loop() -> None:
    """A RuntimeError from run_once is logged as WARNING; the loop exits via CancelledError."""
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_time = MagicMock()
    mock_time.monotonic.side_effect = [0.0, stream_worker.RETENTION_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", AsyncMock()),
        patch(
            "app.workers.stream_worker.retention.run_once",
            AsyncMock(side_effect=RuntimeError("DB down")),
        ),
        patch("app.workers.stream_worker.time", mock_time),
        patch.object(stream_worker, "logger") as mock_logger,
    ):
        # Loop must exit via CancelledError, not RuntimeError.
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    # A warning must have been emitted for the retention failure.
    warning_messages = [
        call.args[0] for call in mock_logger.warning.call_args_list if call.args
    ]
    assert any("retención" in msg for msg in warning_messages)
