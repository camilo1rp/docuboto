# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests for MVP-3 dead-letter retention (ADR-021 Fase 4 / Fase 5 Inc.2) — notification-service.

All tests are pure-unit (no real DB or Redis).  The pool, acquire_tenant, and
DeadLetterRepository are mocked via patch so that each assertion exercises a
single behaviour without infrastructure dependencies.

Invariants verified:
  - purge_terminal: deletes 'resolved' and 'discarded' rows past the grace period.
  - purge_terminal: NEVER deletes 'pending' rows (they await human decision).
  - purge_terminal: scopes by tenant_slug AND origin_group (shared table with
    signature-service and workflow-service — never touches their rows).
  - purge_terminal: raises ValueError for days < 1 (fail-closed guard).
  - purge_deadletter: iterates all active tenants and aggregates counts.
  - purge_deadletter: a failure in one tenant does not abort the others.
  - Worker cadence: deadletter_retention.run_once fires on the same daily gate
    as the MVP-1 dedup purge and the MVP-2 DLQ trim.
  - Worker cadence: a run_once failure logs WARNING and does not crash the loop.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import DLQ_GROUP
from app.jobs import deadletter_retention
from app.repositories.dead_letter_repository import DeadLetterRepository

TENANT = "icetex"
OTHER_GROUP = "workflow-service"  # another consumer sharing evento_dead_letter


# ---------------------------------------------------------------------------
# purge_terminal — repository unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_terminal_returns_deleted_count() -> None:
    """SQL tag 'DELETE 5' → purge_terminal returns 5."""
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 5"
    repo = DeadLetterRepository(conn)
    n = await repo.purge_terminal(90, TENANT, DLQ_GROUP)
    assert n == 5
    sql_call = conn.execute.call_args[0][0]
    assert "resolved" in sql_call
    assert "discarded" in sql_call
    assert "pending" not in sql_call


@pytest.mark.asyncio
async def test_purge_terminal_zero_rows_within_grace() -> None:
    """DB reports DELETE 0 (all terminal rows still within grace period) → returns 0."""
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 0"
    repo = DeadLetterRepository(conn)
    n = await repo.purge_terminal(90, TENANT, DLQ_GROUP)
    assert n == 0


@pytest.mark.asyncio
async def test_purge_terminal_uses_parameterised_interval() -> None:
    """tenant_slug, origin_group and days must be passed as positional parameters
    (never interpolated), in the order the WHERE clause binds them ($1, $2, $3)."""
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 0"
    repo = DeadLetterRepository(conn)
    await repo.purge_terminal(30, TENANT, DLQ_GROUP)
    _, *pos_args = conn.execute.call_args[0]
    assert pos_args == [TENANT, DLQ_GROUP, 30]


@pytest.mark.asyncio
async def test_purge_terminal_scoped_to_own_tenant_and_origin_group() -> None:
    """The WHERE clause binds tenant_slug = $1 AND origin_group = $2 — without
    this predicate a purge would also delete another service's terminal rows
    in the same shared evento_dead_letter table, coupling the retention windows
    (see module note in dead_letter_repository.py)."""
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 1"
    repo = DeadLetterRepository(conn)
    await repo.purge_terminal(90, TENANT, DLQ_GROUP)
    sql_call = conn.execute.call_args[0][0]
    assert "tenant_slug = $1" in sql_call
    assert "origin_group = $2" in sql_call
    _, *pos_args = conn.execute.call_args[0]
    assert pos_args[0] == TENANT
    assert pos_args[1] == DLQ_GROUP
    assert pos_args[1] != OTHER_GROUP


@pytest.mark.asyncio
async def test_purge_terminal_fail_closed_on_zero_days() -> None:
    """days=0 raises ValueError before any DB call (fail-closed guard)."""
    conn = AsyncMock()
    repo = DeadLetterRepository(conn)
    with pytest.raises(ValueError, match="days >= 1"):
        await repo.purge_terminal(0, TENANT, DLQ_GROUP)
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_purge_terminal_fail_closed_on_negative_days() -> None:
    """Negative days raises ValueError before any DB call."""
    conn = AsyncMock()
    repo = DeadLetterRepository(conn)
    with pytest.raises(ValueError):
        await repo.purge_terminal(-1, TENANT, DLQ_GROUP)
    conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# purge_deadletter — job unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_deadletter_aggregates_across_tenants() -> None:
    """purge_deadletter returns a dict mapping slug → deleted rows for each tenant."""
    mock_pool = MagicMock()

    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [
        {"slug": "tenant-a"},
        {"slug": "tenant-b"},
    ]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_conn_a = AsyncMock()
    mock_conn_a.execute.return_value = "DELETE 3"
    mock_conn_b = AsyncMock()
    mock_conn_b.execute.return_value = "DELETE 7"

    call_order = iter([mock_conn_a, mock_conn_b])

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield next(call_order)

    with (
        patch("app.jobs.deadletter_retention.get_pool", return_value=mock_pool),
        patch("app.jobs.deadletter_retention.acquire_tenant", fake_acquire_tenant),
    ):
        result = await deadletter_retention.purge_deadletter(90)

    assert result == {"tenant-a": 3, "tenant-b": 7}
    _, slug_a, group_a, days_a = mock_conn_a.execute.call_args[0]
    assert (slug_a, group_a, days_a) == ("tenant-a", DLQ_GROUP, 90)
    _, slug_b, group_b, days_b = mock_conn_b.execute.call_args[0]
    assert (slug_b, group_b, days_b) == ("tenant-b", DLQ_GROUP, 90)


@pytest.mark.asyncio
async def test_purge_deadletter_failure_in_one_tenant_does_not_abort() -> None:
    """A DB error for tenant-a is caught; tenant-b is still processed."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [
        {"slug": "tenant-a"},
        {"slug": "tenant-b"},
    ]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_conn_a = AsyncMock()
    mock_conn_a.execute.side_effect = RuntimeError("DB connection lost")
    mock_conn_b = AsyncMock()
    mock_conn_b.execute.return_value = "DELETE 2"

    call_order = iter([mock_conn_a, mock_conn_b])

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield next(call_order)

    with (
        patch("app.jobs.deadletter_retention.get_pool", return_value=mock_pool),
        patch("app.jobs.deadletter_retention.acquire_tenant", fake_acquire_tenant),
    ):
        result = await deadletter_retention.purge_deadletter(90)

    assert "tenant-a" not in result
    assert result.get("tenant-b") == 2


@pytest.mark.asyncio
async def test_purge_deadletter_empty_tenant_list() -> None:
    """When there are no active tenants the job returns an empty dict."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = []
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("app.jobs.deadletter_retention.get_pool", return_value=mock_pool):
        result = await deadletter_retention.purge_deadletter(90)

    assert result == {}


@pytest.mark.asyncio
async def test_run_once_uses_configured_days() -> None:
    """run_once calls purge_deadletter with settings.retention_deadletter_days."""
    with patch(
        "app.jobs.deadletter_retention.purge_deadletter", new_callable=AsyncMock
    ) as mock_purge:
        mock_purge.return_value = {}
        with patch("app.jobs.deadletter_retention.settings") as mock_settings:
            mock_settings.retention_deadletter_days = 45
            await deadletter_retention.run_once()
        mock_purge.assert_called_once_with(45)


# ---------------------------------------------------------------------------
# Worker cadence gate tests (app/workers/stream_worker.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_calls_deadletter_retention_run_once_on_daily_gate() -> None:
    """MVP-3: deadletter_retention.run_once fires on the same daily gate as MVP-1/MVP-2."""
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [[], asyncio.CancelledError()]

    mock_time = MagicMock()
    mock_time.monotonic.side_effect = [0.0, stream_worker.RETENTION_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", AsyncMock()),
        patch("app.workers.stream_worker.retention.run_once", AsyncMock()),
        patch("app.workers.stream_worker.events.trim_deadletter", AsyncMock()),
        patch(
            "app.workers.stream_worker.deadletter_retention.run_once",
            new_callable=AsyncMock,
        ) as mock_dlq_retention,
        patch("app.workers.stream_worker.time", mock_time),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    mock_dlq_retention.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_deadletter_retention_failure_does_not_crash_loop() -> None:
    """A deadletter_retention.run_once exception logs WARNING; the loop continues
    (MVP-1 and MVP-2 already ran, and the CancelledError still shuts it down cleanly)."""
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [[], asyncio.CancelledError()]

    mock_time = MagicMock()
    mock_time.monotonic.side_effect = [0.0, stream_worker.RETENTION_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", AsyncMock()),
        patch("app.workers.stream_worker.retention.run_once", AsyncMock()),
        patch("app.workers.stream_worker.events.trim_deadletter", AsyncMock()),
        patch(
            "app.workers.stream_worker.deadletter_retention.run_once",
            AsyncMock(side_effect=RuntimeError("transient DB failure")),
        ),
        patch("app.workers.stream_worker.time", mock_time),
        patch.object(stream_worker, "logger") as mock_logger,
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list if call.args]
    assert any("dead-letter durable" in msg or "dead_letter" in msg for msg in warning_messages)
