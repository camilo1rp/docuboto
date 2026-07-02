# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del router admin DLQ (ADR-021 Fase 5 Inc.2, PERM_DLQ_ADMIN) — notification-service.

Cubre: gate de permiso, resumen sin payload/last_error, gate de clearance (404
neutro), fail-closed sobre nivel vivo, claim-then-send del replay (best-effort
email: 502 si algún envío falla → rollback, permanece pending), enrutamiento por
origin_stream a los builders puros, concurrencia (409), tenant mismatch (422),
scoping por origin_group en la tabla compartida evento_dead_letter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status

from app.core.clearance import assert_can_read_deadletter
from app.core.constants import DLQ_GROUP
from app.models.notification import Notification
from app.repositories.dead_letter_repository import DeadLetterRepository
from app.repositories.processed_events_repository import ProcessedEventsRepository
from app.workers.stream_worker import DOCUMENT_STREAM, WORKFLOW_STREAM
from tests.conftest import TENANT_HEADERS

BASE = "/api/v1/notification/admin/deadletter"
EVT = "eeeeeeee-0000-0000-0000-000000000001"
NOW = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
TRACKING = "2026-ICETEX-E-000001"
TENANT = "icetex"
OTHER_GROUP = "signature-service"  # another consumer sharing evento_dead_letter

# FIX 1: exact body raised both by the router's "row is None" branches AND by
# assert_can_read_deadletter's clearance-denied branch — the two cases MUST be
# indistinguishable (no-read-up, RF-SEG-08). Every test asserting either 404
# reuses this constant so a future edit that de-syncs the two literals fails.
_NOT_FOUND_BODY = {
    "error": "not_found",
    "detail": "Dead-letter event not found or not accessible",
    "status": 404,
}


def _summary_row(origin_stream: str = WORKFLOW_STREAM) -> dict:
    return {
        "event_id": EVT,
        "event_type": "workflow.step.created",
        "origin_stream": origin_stream,
        "origin_group": DLQ_GROUP,
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "total_count": 1,
    }


def _full_row(
    *,
    tenant: str = TENANT,
    origin_stream: str | None = WORKFLOW_STREAM,
    event_type: str = "workflow.step.created",
    inner_payload: dict | None = None,
) -> dict:
    if inner_payload is None:
        inner_payload = {"action": "assign", "tracking_number": TRACKING, "to_dept": "001"}
    return {
        "event_id": EVT,
        "event_type": event_type,
        "correlation_id": "cccc-0001",
        "tenant_slug": tenant,
        "origin_stream": origin_stream,
        "origin_group": DLQ_GROUP,
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "payload": {
            "event_id": EVT,
            "event_type": event_type,
            "correlation_id": "cccc-0001",
            "tenant_slug": tenant,
            "occurred_at": "2026-06-30T09:00:00+00:00",
            "actor": "user-001",
            "payload": inner_payload,
        },
        "failure": {
            "dead_letter_reason": "max_deliveries_exceeded",
            "dead_letter_last_error": "connection refused",
        },
    }


def _sent_notification(status_: str = "sent") -> Notification:
    return Notification(
        id="00000000-0000-0000-0000-000000000099",
        recipient_email="001@orpycamcp.local",
        subject="[OrpycaMCP] Nuevo radicado asignado",
        body="body",
        status=status_,
    )


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
    # FIX 1: row-None body must be byte-identical to the clearance-denied body
    # (see test_assert_can_read_deadletter_denied_body_matches_row_none_body).
    assert resp.json() == {"detail": _NOT_FOUND_BODY}


@pytest.mark.asyncio
async def test_detail_requires_x_user_id(client_with_conn) -> None:
    """get_actor exige X-User-Id; sin él → 401 (independiente del gate de permiso)."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    headers = {"X-Tenant-Slug": TENANT}  # sin X-User-Id
    resp = await client.get(f"{BASE}/{EVT}", headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_detail_clearance_denied(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail={"error": "not_found", "status": 404}
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=1)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter",
            AsyncMock(side_effect=denial),
        ),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 404  # neutro, no 403


@pytest.mark.asyncio
async def test_detail_success(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == EVT
    assert body["payload"]["tenant_slug"] == TENANT
    assert "failure" in body


# --------------------------------------------------------------------------- replay
@pytest.mark.asyncio
async def test_replay_success_workflow_stream(client_with_conn) -> None:
    """Replay de un evento del stream de workflow: 1 email 'sent' → resolved."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row(origin_stream=WORKFLOW_STREAM)
    conn.execute.return_value = "UPDATE 1"
    mock_audit = AsyncMock()
    mock_send = AsyncMock(return_value=_sent_notification("sent"))
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter.send_notification", mock_send),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_status"] == "resolved"
    assert body["previous_status"] == "pending"
    assert body["outcome"]["notifications_sent"] == 1
    mock_send.assert_awaited_once()
    mock_audit.append.assert_called_once()
    assert mock_audit.append.call_args.kwargs["action"] == "evento.dead_letter.replayed"


@pytest.mark.asyncio
async def test_replay_success_document_stream(client_with_conn) -> None:
    """Replay de un evento del stream de document usa build_document_notifications."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row(
        origin_stream=DOCUMENT_STREAM,
        event_type="document.radicado.created",
        inner_payload={"tracking_number": TRACKING, "dest_dept_code": "007"},
    )
    conn.execute.return_value = "UPDATE 1"
    mock_send = AsyncMock(return_value=_sent_notification("sent"))
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter.send_notification", mock_send),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    sent_payload = mock_send.call_args.args[0]
    assert "dependencia-007" in sent_payload.recipient_email


@pytest.mark.asyncio
async def test_replay_clears_dedup_claim_before_send(client_with_conn) -> None:
    """El replay limpia notification_processed_events antes de reenviar (defensivo)."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch(
            "app.routers.admin_deadletter.send_notification",
            AsyncMock(return_value=_sent_notification("sent")),
        ),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    delete_calls = [
        c for c in conn.execute.call_args_list if "notification_processed_events" in c.args[0]
    ]
    assert len(delete_calls) == 1
    assert delete_calls[0].args[1] == EVT


@pytest.mark.asyncio
async def test_replay_smtp_failure_returns_502_and_stays_pending(client_with_conn) -> None:
    """Un envío con status='failed' → 502; el estado NO transiciona a resolved."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    mock_send = AsyncMock(return_value=_sent_notification("failed"))
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter.send_notification", mock_send),
        patch("app.routers.admin_deadletter._audit", AsyncMock()) as mock_audit,
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 502
    body = resp.json()
    assert body["detail"]["error"] == "replay_failed"
    # transition_status must NOT have been reached — the SQL that would UPDATE the
    # status column is never issued; audit.append (which only fires after the
    # transition) must not have been called either.
    update_calls = [c for c in conn.execute.call_args_list if "UPDATE evento_dead_letter" in c.args[0]]
    assert update_calls == []
    mock_audit.append.assert_not_called()


@pytest.mark.asyncio
async def test_replay_handler_raises_returns_502(client_with_conn) -> None:
    """Defensa adicional: si send_notification llegara a lanzar, también es 502."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch(
            "app.routers.admin_deadletter.send_notification",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 502
    body = resp.json()
    # Raw exception must NOT leak to the client.
    assert "boom" not in str(body)
    assert body["detail"]["error"] == "replay_failed"


@pytest.mark.asyncio
async def test_replay_audit_unavailable_returns_503(client_with_conn) -> None:
    """FIX 2(a): sin orpycamcp_common.audit, el replay se rechaza con 503 ANTES
    de enrutar al builder, enviar SMTP o transicionar estado — fail-closed."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    mock_send = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", False),
        patch("app.routers.admin_deadletter.send_notification", mock_send),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "audit_unavailable"
    # No externally-visible effect and no state mutation happened.
    mock_send.assert_not_called()
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_replay_unknown_origin_stream_returns_422(client_with_conn) -> None:
    """origin_stream desconocido/ausente no puede enrutarse → 422, fail-closed."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row(origin_stream="orpycamcp.unknown.events")
    mock_send = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter.send_notification", mock_send),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "unknown_origin_stream"
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_replay_not_pending_returns_409(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 0"  # transition_status → 0 filas
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch(
            "app.routers.admin_deadletter.send_notification",
            AsyncMock(return_value=_sent_notification("sent")),
        ),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_replay_tenant_mismatch_returns_422(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row(tenant="otro-tenant")  # ≠ icetex
    with patch("app.routers.admin_deadletter._audit", AsyncMock()):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_replay_clearance_denied_returns_404(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail={"error": "not_found", "status": 404}
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=1)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter",
            AsyncMock(side_effect=denial),
        ),
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
    # FIX 1: identical body to the clearance-denied 404 (test_replay_clearance_denied_returns_404).
    assert resp.json() == {"detail": _NOT_FOUND_BODY}


# --------------------------------------------------------------------------- discard
@pytest.mark.asyncio
async def test_discard_success(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "spurious duplicate event"}
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
        status_code=status.HTTP_404_NOT_FOUND, detail={"error": "not_found", "status": 404}
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=1)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter",
            AsyncMock(side_effect=denial),
        ),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "spurious duplicate event"}
        )
    assert resp.status_code == 404  # neutro, no 403
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_discard_audit_unavailable_returns_503(client_with_conn) -> None:
    """FIX 2(a): sin orpycamcp_common.audit, el discard se rechaza con 503 ANTES
    de transicionar pending → discarded — fail-closed (no traza legal, no estado)."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", False),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "spurious duplicate event"}
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "audit_unavailable"
    conn.execute.assert_not_called()  # no state mutation without a durable trace


@pytest.mark.asyncio
async def test_discard_not_pending_returns_409(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = {
        "event_id": EVT,
        "status": "resolved",
        "event_type": "x",
        "origin_stream": "y",
        "failure": {},
    }
    conn.execute.return_value = "UPDATE 0"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "late arrival, discarding"}
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_discard_not_found_returns_404(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = None
    resp = await client.post(f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "manual test discard"})
    assert resp.status_code == 404
    # FIX 1: identical body to the clearance-denied 404 (test_discard_clearance_denied_returns_404).
    assert resp.json() == {"detail": _NOT_FOUND_BODY}


@pytest.mark.asyncio
async def test_discard_reason_too_short_returns_422(client_with_conn) -> None:
    """FIX 4: motivo sustantivo — un reason con menos de 10 caracteres es rechazado
    por el schema (antes de llegar al gate de clearance o a la BD)."""
    client, conn = client_with_conn
    resp = await client.post(f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "short"})
    assert resp.status_code == 422
    conn.fetchrow.assert_not_called()  # rejected by pydantic before touching the DB


@pytest.mark.asyncio
async def test_discard_reason_whitespace_only_returns_422(client_with_conn) -> None:
    """FIX 4: pydantic v2 aplica strip_whitespace ANTES de contar min_length — un
    reason que es puro espacio en blanco (incluso si su longitud cruda es >= 10)
    debe ser rechazado, no aceptado como motivo sustantivo."""
    client, conn = client_with_conn
    resp = await client.post(
        f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "            "}
    )
    assert resp.status_code == 422
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_discard_reason_stripped_before_storage(client_with_conn) -> None:
    """FIX 4: un reason válido con espacio sobrante se guarda ya recortado en el
    payload de auditoría (StringConstraints strip_whitespace=True)."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard",
            headers=TENANT_HEADERS,
            json={"reason": "  duplicate event  "},
        )
    assert resp.status_code == 200
    assert mock_audit.append.call_args.kwargs["payload"]["reason"] == "duplicate event"


# ------------------------------------------- origin_group scoping (shared table)
# `evento_dead_letter` is shared with signature-service and workflow-service in
# the same tenant schema (see module note in
# app/repositories/dead_letter_repository.py). These tests cover both ends:
# (a) the router always queries with ITS OWN origin_group (wiring), and (b) the
# repository methods actually filter rows by origin_group so a row belonging to
# another consumer is invisible here.

@pytest.mark.asyncio
async def test_list_passes_own_origin_group_to_repo(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [_summary_row()]
    await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    params = conn.fetch.call_args[0][1:]
    assert DLQ_GROUP in params
    assert OTHER_GROUP not in params


@pytest.mark.asyncio
async def test_detail_passes_own_origin_group_to_repo(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
    ):
        await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    # get_by_event_id(event_id, caller_slug, origin_group)
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_replay_uses_get_for_update_with_own_origin_group(client_with_conn) -> None:
    """Concurrencia (H7): el replay bloquea la fila con FOR UPDATE + origin_group propio."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch(
            "app.routers.admin_deadletter.send_notification",
            AsyncMock(return_value=_sent_notification("sent")),
        ),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    # get_for_update(event_id, caller_slug, origin_group) — SELECT ... FOR UPDATE
    fetchrow_call = conn.fetchrow.call_args_list[-1]
    assert "FOR UPDATE" in fetchrow_call.args[0]
    assert fetchrow_call.args[-1] == DLQ_GROUP
    # transition_status(event_id, caller_slug, new_status, origin_group)
    transition_calls = [
        c for c in conn.execute.call_args_list if "UPDATE evento_dead_letter" in c.args[0]
    ]
    assert len(transition_calls) == 1
    assert transition_calls[0].args[-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_discard_passes_own_origin_group(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance", AsyncMock(return_value=3)),
        patch(
            "app.routers.admin_deadletter.assert_can_read_deadletter", AsyncMock(return_value=None)
        ),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        await client.post(f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "manual test discard"})
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP
    assert conn.execute.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_repo_get_by_event_id_filters_by_origin_group() -> None:
    """Repository-level: una fila del mismo event_id/tenant pero DISTINTO
    origin_group (la fila del otro consumidor en la tabla compartida) no debe
    ser visible — comportamiento real de filtrado, no solo wiring de llamada."""
    table = {
        (EVT, TENANT, DLQ_GROUP): {"event_id": EVT, "origin_group": DLQ_GROUP},
        (EVT, TENANT, OTHER_GROUP): {"event_id": EVT, "origin_group": OTHER_GROUP},
    }

    async def fake_fetchrow(_sql, event_id, caller_slug, origin_group):
        return table.get((event_id, caller_slug, origin_group))

    conn = AsyncMock()
    conn.fetchrow = fake_fetchrow
    repo = DeadLetterRepository(conn)

    own_row = await repo.get_by_event_id(EVT, TENANT, DLQ_GROUP)
    assert own_row is not None and own_row["origin_group"] == DLQ_GROUP

    cross_row = await repo.get_by_event_id(EVT, TENANT, OTHER_GROUP)
    assert cross_row is not None and cross_row["origin_group"] == OTHER_GROUP
    assert cross_row != own_row


@pytest.mark.asyncio
async def test_repo_transition_status_does_not_affect_other_origin_group() -> None:
    """transition_status's UPDATE … WHERE origin_group = $N debe reportar 0 filas
    cuando el id coincide pero el origin_group pertenece a otro consumidor."""
    conn = AsyncMock()

    async def fake_execute(_sql, event_id, caller_slug, new_status, origin_group):
        if origin_group != DLQ_GROUP:
            return "UPDATE 0"
        return "UPDATE 1"

    conn.execute = fake_execute
    repo = DeadLetterRepository(conn)

    rows_own = await repo.transition_status(EVT, TENANT, "resolved", DLQ_GROUP)
    assert rows_own == 1

    rows_other = await repo.transition_status(EVT, TENANT, "resolved", OTHER_GROUP)
    assert rows_other == 0


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
async def test_assert_can_read_deadletter_denied_body_matches_row_none_body() -> None:
    """FIX 1: el cuerpo 404 emitido por assert_can_read_deadletter (clearance
    insuficiente, radicado real hallado con nivel alto) debe ser byte-idéntico
    al ``_NOT_FOUND_BODY`` que los routers usan para su rama "row is None" —
    de lo contrario un atacante podría distinguir "no existe" de "existe pero
    sin clearance" comparando cuerpos de respuesta (RF-SEG-08, no-read-up)."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=3)  # nivel_vivo=CLASIFICADA, radicado existe
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}

    with pytest.raises(HTTPException) as exc:
        await assert_can_read_deadletter(conn, caller_clearance=1, row=row)

    assert exc.value.status_code == 404
    assert exc.value.detail == _NOT_FOUND_BODY


@pytest.mark.asyncio
async def test_fail_closed_nivel_max_clearance_passes() -> None:
    """Aun con radicado no hallado, un llamante CLASIFICADA (3) no es bloqueado."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}
    await assert_can_read_deadletter(conn, caller_clearance=3, row=row)  # no debe lanzar


# --------------------------------------------------- ProcessedEventsRepository.delete_one
@pytest.mark.asyncio
async def test_processed_events_delete_one_returns_deleted_count() -> None:
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 1"
    repo = ProcessedEventsRepository(conn)
    n = await repo.delete_one(EVT)
    assert n == 1
    sql_call = conn.execute.call_args[0]
    assert "notification_processed_events" in sql_call[0]
    assert sql_call[1] == EVT


@pytest.mark.asyncio
async def test_processed_events_delete_one_no_row_returns_zero() -> None:
    """El caso más común en la práctica: el event_id nunca llegó a reclamarse."""
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 0"
    repo = ProcessedEventsRepository(conn)
    n = await repo.delete_one(EVT)
    assert n == 0
