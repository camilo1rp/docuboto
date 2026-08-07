# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de dead-letter y barrido de reclamación en notification-service (ADR-021 Fase 3).

Cubre:
  1. _persist_dead_letter() — INSERT real + audit.append en la misma transacción.
  2. _persist_dead_letter() — idempotencia ON CONFLICT (no re-audita cuando None).
  3. _persist_dead_letter() — error transiente de BD propaga (R-4: no ack sin traza).
  4. _persist_dead_letter() — R1: envelope sin tenant_slug → audita bajo SYSTEM_TENANT
     (tenant_slug=None) vía public pool y retorna normalmente.
  5. _persist_dead_letter() — R1 sin audit disponible: retorna sin lanzar.

Patrón de mock:
  - acquire_tenant se parchea como asynccontextmanager que devuelve un AsyncMock.
  - _audit se parchea a nivel de módulo (app.workers.stream_worker._audit).
  - get_pool().acquire() se parchea para el caso R1 (sin tenant schema).
  - conn.transaction() se parchea como asynccontextmanager.
  - conn.fetchrow controla el resultado del INSERT de evento_dead_letter.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT = "demo"
EVENT_ID = "evt-dead-0001"


def _make_conn(*, fetchrow_return: object = None) -> AsyncMock:
    """Mock asyncpg connection con transaction() como asynccontextmanager."""
    conn = AsyncMock()
    # transaction() must be an async context manager (used as `async with conn.transaction():`).
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchrow.return_value = fetchrow_return
    return conn


def _fake_acquire_tenant(conn: AsyncMock):
    @asynccontextmanager
    async def _ctx(tenant_slug: str):  # noqa: ARG001
        yield conn

    return _ctx


def _make_failure_meta(*, origin_stream: str = "orpycamcp.workflow.events") -> dict:
    return {
        "dead_letter_reason": "max_deliveries_exceeded",
        "dead_letter_origin_stream": origin_stream,
        "dead_letter_group": "notification-service",
        "dead_letter_consumer": "notification-service-1",
        "dead_letter_delivery_count": "5",
        "dead_lettered_at": "2026-06-30T10:00:00+00:00",
        "dead_letter_last_error": "",
    }


def _make_envelope(*, tenant_slug: str = TENANT) -> dict:
    return {
        "event_id": EVENT_ID,
        "event_type": "workflow.step.created",
        "correlation_id": "corr-0001",
        "tenant_slug": tenant_slug,
        "occurred_at": "2026-06-30T10:00:00+00:00",
        "actor": "user-001",
        "payload": {"tracking_number": "2026-DEMO-E-000001", "to_dept": "001"},
    }


# ---------------------------------------------------------------------------
# Tests — _persist_dead_letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_dead_letter_insert_real_y_audit() -> None:
    """INSERT real → repo.insert_if_new devuelve fila → audit.append es llamado."""
    from app.workers import stream_worker

    conn = _make_conn(fetchrow_return={"event_id": EVENT_ID})  # INSERT real
    mock_audit = AsyncMock()

    with (
        patch.object(stream_worker, "acquire_tenant", _fake_acquire_tenant(conn)),
        patch.object(stream_worker, "_audit", mock_audit),
        patch.object(stream_worker, "_AUDIT_AVAILABLE", True),
    ):
        await stream_worker._persist_dead_letter(
            _make_envelope(), _make_failure_meta()
        )

    # audit.append debe haberse llamado exactamente una vez (dentro de la tx).
    mock_audit.append.assert_called_once()
    call_kwargs = mock_audit.append.call_args.kwargs
    assert call_kwargs["action"] == "evento.dead_letter"
    assert call_kwargs["actor"] == "system"
    assert call_kwargs["tenant_slug"] == TENANT


@pytest.mark.asyncio
async def test_persist_dead_letter_idempotente_no_re_audita() -> None:
    """ON CONFLICT (insert_if_new devuelve None) → audit.append NO es llamado."""
    from app.workers import stream_worker

    conn = _make_conn(fetchrow_return=None)  # ON CONFLICT → idempotent no-op
    mock_audit = AsyncMock()

    with (
        patch.object(stream_worker, "acquire_tenant", _fake_acquire_tenant(conn)),
        patch.object(stream_worker, "_audit", mock_audit),
        patch.object(stream_worker, "_AUDIT_AVAILABLE", True),
    ):
        await stream_worker._persist_dead_letter(
            _make_envelope(), _make_failure_meta()
        )

    mock_audit.append.assert_not_called()


@pytest.mark.asyncio
async def test_persist_dead_letter_error_transitorio_propaga() -> None:
    """Error de BD transitorio propaga (R-4: no ack sin traza durable)."""
    from app.workers import stream_worker

    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchrow.side_effect = OSError("pool exhausted")

    with patch.object(stream_worker, "acquire_tenant", _fake_acquire_tenant(conn)):
        with pytest.raises(OSError, match="pool exhausted"):
            await stream_worker._persist_dead_letter(
                _make_envelope(), _make_failure_meta()
            )


@pytest.mark.asyncio
async def test_persist_dead_letter_insert_real_sin_audit_disponible_propaga() -> None:
    """FIX 2(b): INSERT real + _AUDIT_AVAILABLE=False → propaga (no XACK, no
    persistido sin traza) en lugar de comitear el INSERT sin fila de auditoría."""
    from app.workers import stream_worker

    conn = _make_conn(fetchrow_return={"event_id": EVENT_ID})  # INSERT real
    mock_audit = AsyncMock()

    with (
        patch.object(stream_worker, "acquire_tenant", _fake_acquire_tenant(conn)),
        patch.object(stream_worker, "_audit", mock_audit),
        patch.object(stream_worker, "_AUDIT_AVAILABLE", False),
    ):
        with pytest.raises(RuntimeError, match="audit"):
            await stream_worker._persist_dead_letter(
                _make_envelope(), _make_failure_meta()
            )

    mock_audit.append.assert_not_called()


@pytest.mark.asyncio
async def test_persist_dead_letter_r1_sin_tenant_slug_audita_bajo_system_tenant() -> None:
    """R1: envelope sin tenant_slug → audit bajo SYSTEM_TENANT (tenant_slug=None) en public pool.

    La función retorna normalmente para que el helper pueda XACK el poison pill.
    Loopear infinitamente en PEL por un evento sin tenant_slug sería inútil.
    """
    from app.workers import stream_worker

    # Simulate the public pool connection (no tenant schema).
    public_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=public_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_audit = AsyncMock()
    envelope_no_tenant = _make_envelope(tenant_slug="")
    envelope_no_tenant["tenant_slug"] = ""  # explicitly empty

    with (
        patch.object(stream_worker, "get_pool", return_value=mock_pool),
        patch.object(stream_worker, "_audit", mock_audit),
        patch.object(stream_worker, "_AUDIT_AVAILABLE", True),
    ):
        # Must return normally (not raise) — poison pill should be acked.
        await stream_worker._persist_dead_letter(
            envelope_no_tenant, _make_failure_meta()
        )

    # audit.append must have been called with tenant_slug=None (→ SYSTEM_TENANT).
    mock_audit.append.assert_called_once()
    call_kwargs = mock_audit.append.call_args.kwargs
    assert call_kwargs["tenant_slug"] is None
    assert call_kwargs["action"] == "evento.dead_letter"
    assert call_kwargs["actor"] == "system"


@pytest.mark.asyncio
async def test_persist_dead_letter_r1_sin_audit_disponible_retorna_sin_lanzar() -> None:
    """R1 sin módulo de auditoría: retorna sin lanzar (poison pill sigue siendo acked)."""
    from app.workers import stream_worker

    envelope_no_tenant = _make_envelope(tenant_slug="")
    envelope_no_tenant["tenant_slug"] = ""

    with patch.object(stream_worker, "_AUDIT_AVAILABLE", False):
        # Must return normally even when audit module is not available.
        await stream_worker._persist_dead_letter(
            envelope_no_tenant, _make_failure_meta()
        )
    # No exception raised → test passes.


@pytest.mark.asyncio
async def test_persist_dead_letter_origin_stream_desde_failure_meta() -> None:
    """origin_stream en failure_meta se pasa correctamente a insert_if_new."""
    from app.workers import stream_worker

    conn = _make_conn(fetchrow_return={"event_id": EVENT_ID})
    mock_audit = AsyncMock()
    failure = _make_failure_meta(origin_stream="orpycamcp.document.events")

    with (
        patch.object(stream_worker, "acquire_tenant", _fake_acquire_tenant(conn)),
        patch.object(stream_worker, "_audit", mock_audit),
        patch.object(stream_worker, "_AUDIT_AVAILABLE", True),
    ):
        await stream_worker._persist_dead_letter(_make_envelope(), failure)

    # Verify the INSERT was called with origin_stream from failure_meta.
    # conn.fetchrow receives positional args: event_id, event_type, correlation_id,
    # tenant_slug, payload_json, failure_json, origin_stream, origin_group.
    call_args = conn.fetchrow.call_args
    # origin_stream is the 8th positional arg (index 7, after the SQL string at index 0).
    assert call_args.args[7] == "orpycamcp.document.events"
