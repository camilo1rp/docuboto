# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del router admin DLQ (ADR-021 Fase 5, PERM_DLQ_ADMIN) — signature-service.

Cubre: gate de permiso, resumen sin payload/last_error, gate de clearance (404
neutro), fail-closed sobre nivel vivo, atomicidad del replay (502 → rollback),
concurrencia (409), y tenant mismatch (422).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status

from app.core.clearance import assert_can_read_deadletter
from tests.conftest import TENANT_HEADERS

BASE = "/api/v1/signature/admin/deadletter"
EVT = "eeeeeeee-0000-0000-0000-000000000001"
NOW = datetime(2026, 6, 29, 10, 0, tzinfo=UTC)
TRACKING = "2024-DEMO-E-000001"


def _summary_row() -> dict:
    return {
        "event_id": EVT,
        "event_type": "document.radicado.reclassified",
        "origin_stream": "orpycamcp.document.events",
        "origin_group": "signature-service",
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "total_count": 1,
    }


def _full_row(tenant: str = "demo", nivel_nuevo: int = 3) -> dict:
    return {
        "event_id": EVT,
        "event_type": "document.radicado.reclassified",
        "correlation_id": "cccc-0001",
        "tenant_slug": tenant,
        "origin_stream": "orpycamcp.document.events",
        "origin_group": "signature-service",
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "payload": {
            "event_id": EVT,
            "tenant_slug": tenant,
            "payload": {
                "tracking_number": TRACKING,
                "id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                "nivel_nuevo": nivel_nuevo,
            },
        },
        "failure": {"dead_letter_reason": "max_deliveries_exceeded",
                    "dead_letter_last_error": "connection refused"},
    }


# --------------------------------------------------------------------------- list
@pytest.mark.asyncio
async def test_list_requires_perm_dlq_admin(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = None  # check_permission → denegado
    resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_returns_summaries(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [_summary_row()]
    resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_id"] == EVT
    assert resp.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_list_no_payload_or_last_error_in_response(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [_summary_row()]
    resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    item = resp.json()[0]
    assert "payload" not in item
    assert "tracking_number" not in item
    assert "last_error" not in item
    assert "failure" not in item


# --------------------------------------------------------------------------- detail
@pytest.mark.asyncio
async def test_detail_requires_perm(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = None
    resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_detail_not_found(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = None
    resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_detail_clearance_denied(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "status": 404},
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(side_effect=denial)),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 404  # neutro, no 403


@pytest.mark.asyncio
async def test_detail_success(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == EVT
    assert body["payload"]["tenant_slug"] == "demo"
    assert "failure" in body


# --------------------------------------------------------------------------- replay
@pytest.mark.asyncio
async def test_replay_success(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_reclassification",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_status"] == "resolved"
    assert body["previous_status"] == "pending"
    mock_audit.append.assert_called_once()
    assert mock_audit.append.call_args.kwargs["action"] == "evento.dead_letter.replayed"


@pytest.mark.asyncio
async def test_replay_handler_fails_returns_502(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_reclassification",
              AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 502
    body = resp.json()
    # Raw exception must NOT leak to the client (Seguridad #2).
    assert "boom" not in str(body)
    assert body["detail"]["error"] == "replay_failed"
    assert "pendiente" in body["detail"]["detail"]


@pytest.mark.asyncio
async def test_replay_not_pending_returns_409(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 0"  # transition_status → 0 filas
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_reclassification",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_replay_tenant_mismatch_returns_422(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row(tenant="otro")  # ≠ demo
    with patch("app.routers.admin_deadletter._audit", AsyncMock()):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_replay_clearance_denied_returns_404(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "status": 404},
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(side_effect=denial)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_not_found_returns_404(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = None
    with patch("app.routers.admin_deadletter._audit", AsyncMock()):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- discard
@pytest.mark.asyncio
async def test_discard_success(client_with_conn) -> None:
    client, conn = client_with_conn
    # get_for_update returns a full row (SELECT *); clearance gate passes.
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "spurious"}
        )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "discarded"
    mock_audit.append.assert_called_once()
    assert mock_audit.append.call_args.kwargs["action"] == "evento.dead_letter.discarded"


@pytest.mark.asyncio
async def test_discard_no_reason_returns_422(client_with_conn) -> None:
    client, _conn = client_with_conn
    resp = await client.post(f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_discard_clearance_denied_returns_404(client_with_conn) -> None:
    """Admin con clearance insuficiente recibe 404 neutro; el estado no transiciona."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "status": 404},
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(side_effect=denial)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "spurious"}
        )
    assert resp.status_code == 404  # neutro, no 403
    # transition_status must NOT have been called → row stays pending.
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_discard_not_pending_returns_409(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = {
        "event_id": EVT, "status": "resolved",
        "event_type": "x", "origin_stream": "y", "failure": {},
    }
    conn.execute.return_value = "UPDATE 0"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "late"}
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_discard_not_found_returns_404(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = None
    resp = await client.post(
        f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "x"}
    )
    assert resp.status_code == 404


# --------------------------------------------------- assert_can_read_deadletter (unit)
@pytest.mark.asyncio
async def test_fail_closed_nivel_radicado_not_found() -> None:
    """Radicado no hallado (fetchval None) → fail-closed CLASIFICADA → 404 si clearance < 3."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # radicado no encontrado
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}

    with pytest.raises(HTTPException) as exc:
        await assert_can_read_deadletter(conn, caller_clearance=1, row=row)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_fail_closed_nivel_max_clearance_passes() -> None:
    """Aun con radicado no hallado, un llamante CLASIFICADA (3) no es bloqueado."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}
    # No debe lanzar.
    await assert_can_read_deadletter(conn, caller_clearance=3, row=row)
