# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de dead-letter durable del worker de ingesta en knowledge-service (E21).

Espejo de workflow-service/tests/test_dead_letter.py, adaptado al MVP de
knowledge-service (sin `doc_id` en el payload de negocio — el evento de dominio
consumido aquí solo expone `tracking_number`).

Cubre:
  1. _persist_dead_letter() — INSERT real + audit.append en la misma transacción.
  2. _persist_dead_letter() — idempotencia ON CONFLICT (no re-audita).
  3. _persist_dead_letter() — error transiente de BD propaga (no ack sin traza).
  4. _persist_dead_letter() — envelope sin tenant_slug → audita bajo SYSTEM_TENANT.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TENANT = "demo"
TRACKING = "2026-DEMO-E-000001"


def _make_conn(*, fetchrow_return: object = None) -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchrow.return_value = fetchrow_return
    return conn


def _fake_acquire_tenant(conn: AsyncMock):
    @asynccontextmanager
    async def _ctx(tenant_slug: str):  # noqa: ARG001
        yield conn

    return _ctx


def _dead_letter_envelope(*, tenant_slug: str = TENANT) -> dict:
    return {
        "event_id": "evt-dead-0001",
        "event_type": "document.radicado.created",
        "correlation_id": "corr-0001",
        "tenant_slug": tenant_slug,
        "occurred_at": "2026-06-29T10:00:00+00:00",
        "actor": "user-original-99",
        "payload": {
            "tracking_number": TRACKING,
            "nivel_seguridad": 1,
        },
    }


def _failure_meta() -> dict:
    return {
        "dead_letter_reason": "max_deliveries_exceeded",
        "dead_letter_origin_stream": "orpycamcp.document.events",
        "dead_letter_group": "knowledge-ingest",
        "dead_letter_consumer": "knowledge-service-1",
        "dead_letter_delivery_count": "5",
        "dead_lettered_at": "2026-06-29T10:01:00+00:00",
        "dead_letter_last_error": "connection reset by peer",
    }


@pytest.mark.asyncio
async def test_persist_dead_letter_inserta_y_audita() -> None:
    from app.workers.document_consumer import _persist_dead_letter

    conn = _make_conn(fetchrow_return={"event_id": "evt-dead-0001"})
    mock_audit = AsyncMock()

    with (
        patch("app.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("app.workers.document_consumer._audit", mock_audit),
        patch("app.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _persist_dead_letter(_dead_letter_envelope(), _failure_meta())

    conn.fetchrow.assert_called_once()
    mock_audit.append.assert_called_once()
    kw = mock_audit.append.call_args.kwargs

    assert kw["actor"] == "system"
    assert kw["canal"] == "system"
    assert kw["action"] == "evento.dead_letter"
    assert kw["object_ref"] == "evt-dead-0001"
    assert kw["tenant_slug"] == TENANT

    payload = kw["payload"]
    assert payload["failure"]["dead_letter_last_error"] == "connection reset by peer"
    assert payload["envelope"]["actor"] == "user-original-99"
    assert payload["envelope"]["payload"]["tracking_number"] == TRACKING
    assert payload["business"]["tracking_number"] == TRACKING


@pytest.mark.asyncio
async def test_persist_dead_letter_idempotente_no_audita_en_conflict() -> None:
    from app.workers.document_consumer import _persist_dead_letter

    conn = _make_conn(fetchrow_return=None)
    mock_audit = AsyncMock()

    with (
        patch("app.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("app.workers.document_consumer._audit", mock_audit),
        patch("app.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _persist_dead_letter(_dead_letter_envelope(), _failure_meta())

    conn.fetchrow.assert_called_once()
    mock_audit.append.assert_not_called()


@pytest.mark.asyncio
async def test_persist_dead_letter_error_transiente_propaga() -> None:
    from app.workers.document_consumer import _persist_dead_letter

    conn = _make_conn()
    conn.fetchrow.side_effect = OSError("connection reset by peer")

    with patch("app.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)):
        with pytest.raises(OSError, match="connection reset by peer"):
            await _persist_dead_letter(_dead_letter_envelope(), _failure_meta())


@pytest.mark.asyncio
async def test_persist_dead_letter_sin_tenant_slug_audita_system_y_retorna() -> None:
    from app.workers.document_consumer import _persist_dead_letter

    mock_audit = AsyncMock()
    mock_pub_conn = AsyncMock()
    mock_pub_conn.transaction = MagicMock(return_value=AsyncMock())
    mock_acquire_cm = MagicMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_pub_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire_cm

    envelope = {
        "event_id": "poison-pill-no-tenant",
        "event_type": "document.radicado.created",
        "actor": "user-x",
        "payload": {"tracking_number": TRACKING},
        # SIN tenant_slug
    }

    with (
        patch("app.workers.document_consumer._audit", mock_audit),
        patch("app.workers.document_consumer._AUDIT_AVAILABLE", True),
        patch("app.workers.document_consumer.get_pool", return_value=mock_pool),
        patch("app.workers.document_consumer.acquire_tenant") as mock_acq,
    ):
        await _persist_dead_letter(envelope, _failure_meta())

    mock_acq.assert_not_called()
    mock_audit.append.assert_called_once()
    kw = mock_audit.append.call_args.kwargs
    assert kw["tenant_slug"] is None
    assert kw["actor"] == "system"
    assert kw["action"] == "evento.dead_letter"
    assert kw["object_ref"] == "poison-pill-no-tenant"
    assert "SYSTEM_TENANT" in kw["payload"]["note"]


@pytest.mark.asyncio
async def test_persist_dead_letter_sin_tenant_slug_error_transiente_propaga() -> None:
    from app.workers.document_consumer import _persist_dead_letter

    mock_audit = AsyncMock()
    mock_audit.append.side_effect = OSError("postgres unavailable")

    mock_pub_conn = AsyncMock()
    mock_acquire_cm = MagicMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_pub_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire_cm

    envelope = {"event_id": "poison-pill-2", "payload": {}}  # sin tenant_slug

    with (
        patch("app.workers.document_consumer._audit", mock_audit),
        patch("app.workers.document_consumer._AUDIT_AVAILABLE", True),
        patch("app.workers.document_consumer.get_pool", return_value=mock_pool),
    ):
        with pytest.raises(OSError, match="postgres unavailable"):
            await _persist_dead_letter(envelope, {"dead_letter_reason": "max_deliveries_exceeded"})
