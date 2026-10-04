# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del router admin DLQ (ADR-021 Fase 5, PERM_DLQ_ADMIN) — knowledge-service (AREA-10-10).

Cubre: gate de permiso, resumen sin payload/last_error, gate de clearance (404
neutro), fail-closed sobre nivel vivo, atomicidad del replay (502 → rollback),
concurrencia (409), y tenant mismatch (422).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException, status

from app.core.clearance import (
    assert_can_read_deadletter,
    resolve_deadletter_level,
    resolve_deadletter_levels,
)
from app.repositories.dead_letter_repository import (
    DEADLETTER_CANDIDATE_CAP,
    DeadLetterRepository,
)
from app.workers.document_consumer import GROUP as DLQ_GROUP

USER = "11111111-1111-1111-1111-111111111111"
TENANT_HEADERS = {"X-Tenant-Slug": "demo", "X-User-Id": USER}

BASE = "/api/v1/knowledge/admin/deadletter"
EVT = "eeeeeeee-0000-0000-0000-000000000001"
NOW = datetime(2026, 6, 29, 10, 0, tzinfo=UTC)
TRACKING = "2024-DEMO-E-000001"
OTHER_GROUP = "signature-service"  # the other consumer sharing evento_dead_letter


def _summary_row() -> dict:
    return {
        "event_id": EVT,
        "event_type": "document.radicado.reclassified",
        "origin_stream": "orpycamcp.document.events",
        "origin_group": "knowledge-ingest",
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "total_count": 1,
    }


def _candidate_row(event_id: str = EVT) -> dict:
    """Fila devuelta por ``list_scoped_for_clearance`` — INCLUYE payload/correlation_id/
    tenant_slug (a diferencia de ``_summary_row``), necesarios para que el router
    resuelva el nivel de seguridad con ``resolve_deadletter_level`` y para que el
    strip de ``payload``/``failure`` antes de serializar tenga algo que descartar."""
    return {
        "event_id": event_id,
        "event_type": "document.radicado.reclassified",
        "correlation_id": "cccc-0001",
        "tenant_slug": "demo",
        "origin_stream": "orpycamcp.document.events",
        "origin_group": "knowledge-ingest",
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "payload": {
            "event_id": event_id,
            "tenant_slug": "demo",
            "payload": {"tracking_number": TRACKING},
        },
    }


def _full_row(tenant: str = "demo", nivel_nuevo: int = 3) -> dict:
    return {
        "event_id": EVT,
        "event_type": "document.radicado.reclassified",
        "correlation_id": "cccc-0001",
        "tenant_slug": tenant,
        "origin_stream": "orpycamcp.document.events",
        "origin_group": "knowledge-ingest",
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
    conn.fetch.return_value = [_candidate_row()]
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={EVT: 1})),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_id"] == EVT
    assert resp.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_list_no_payload_or_last_error_in_response(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [_candidate_row()]
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={EVT: 1})),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    item = resp.json()[0]
    assert "payload" not in item
    assert "tracking_number" not in item
    assert "last_error" not in item
    assert "failure" not in item


# ---------------------------------------- list: filtering by clearance (ADR-021, seguimiento)
@pytest.mark.asyncio
async def test_list_excludes_row_above_caller_clearance(client_with_conn) -> None:
    """Admin con clearance=1 no ve una fila cuyo objeto resuelve a nivel=3; el
    conteo (X-Total-Count) refleja el conjunto YA filtrado (no revela la fila oculta)."""
    client, conn = client_with_conn
    visible_row = _candidate_row("eeeeeeee-0000-0000-0000-000000000001")
    hidden_row = _candidate_row("eeeeeeee-0000-0000-0000-000000000002")
    conn.fetch.return_value = [hidden_row, visible_row]

    levels = {hidden_row["event_id"]: 3, visible_row["event_id"]: 1}

    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value=levels)),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_id"] == visible_row["event_id"]
    assert resp.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_list_fail_closed_on_level_resolution_error(client_with_conn) -> None:
    """Una fila que el batch no pudo resolver con certeza vuelve marcada
    CLASIFICADA (fail-closed, ver test de equivalencia del batch más abajo):
    un admin sub-clearance NO la ve."""
    client, conn = client_with_conn
    conn.fetch.return_value = [_candidate_row()]

    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={EVT: 3})),  # CLASIFICADA
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_list_fail_closed_error_still_visible_to_top_clearance(client_with_conn) -> None:
    """Fail-closed trata la fila como CLASIFICADA=3, no como 'siempre oculta': un
    admin CLASIFICADA (que ya puede ver nivel 3) SÍ la ve."""
    client, conn = client_with_conn
    conn.fetch.return_value = [_candidate_row()]

    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={EVT: 3})),  # CLASIFICADA
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_list_pagination_applied_after_clearance_filter(client_with_conn) -> None:
    """La paginación (page/size) se aplica sobre el conjunto YA filtrado por
    clearance, no sobre las filas crudas devueltas por el repositorio."""
    client, conn = client_with_conn
    rows = [_candidate_row(f"eeeeeeee-0000-0000-0000-{i:012d}") for i in range(5)]
    conn.fetch.return_value = rows

    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={r["event_id"]: 1 for r in rows})),
    ):
        resp = await client.get(f"{BASE}/?page=2&size=2", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert [item["event_id"] for item in data] == [rows[2]["event_id"], rows[3]["event_id"]]
    assert resp.headers["X-Total-Count"] == "5"


# ------------------------------------------------ list: cap signaling (ADR-021, Baja [1])
@pytest.mark.asyncio
async def test_list_sets_x_truncated_header_when_cap_reached(client_with_conn) -> None:
    """"El repositorio pide cap + 1 filas: MÁS de cap candidatas prueba que el listado
    quedó truncado y la respuesta lo señala con X-Truncated (AREA-10-08)."""
    client, conn = client_with_conn
    rows = [
        _candidate_row(f"eeeeeeee-0000-0000-0000-{i:012d}")
        for i in range(DEADLETTER_CANDIDATE_CAP + 1)
    ]
    conn.fetch.return_value = rows
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={r["event_id"]: 1 for r in rows})),
    ):
        resp = await client.get(f"{BASE}/?size=1", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["X-Truncated"] == "true"
    # la fila sobrante (cap + 1) solo sirve para detectar el truncado: no se cuenta ni se entrega.
    assert resp.headers["X-Total-Count"] == str(DEADLETTER_CANDIDATE_CAP)


@pytest.mark.asyncio
async def test_list_exactly_cap_does_not_set_x_truncated_header_when_cap_reached(client_with_conn) -> None:
    """AREA-10-08: con EXACTAMENTE cap candidatas no hay nada fuera de la ventana, así
    que X-Truncated no se emite (antes saltaba con ``len == cap``: falso positivo)."""
    client, conn = client_with_conn
    rows = [
        _candidate_row(f"eeeeeeee-0000-0000-0000-{i:012d}")
        for i in range(DEADLETTER_CANDIDATE_CAP)
    ]
    conn.fetch.return_value = rows
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={r["event_id"]: 1 for r in rows})),
    ):
        resp = await client.get(f"{BASE}/?size=1", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert "X-Truncated" not in resp.headers


@pytest.mark.asyncio
async def test_list_no_x_truncated_header_under_cap(client_with_conn) -> None:
    """Bajo el tope, la respuesta NO fija X-Truncated (ausencia = no truncado,
    nunca se emite 'false')."""
    client, conn = client_with_conn
    conn.fetch.return_value = [_candidate_row()]
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={EVT: 1})),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert "X-Truncated" not in resp.headers


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
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == EVT
    assert body["payload"]["tenant_slug"] == "demo"
    assert "failure" in body


@pytest.mark.asyncio
async def test_detail_success_writes_viewed_audit(client_with_conn) -> None:
    """Un GET autorizado deja asiento ``evento.dead_letter.viewed`` con actor humano
    y un payload mínimo (sin filtrar contenido de negocio/clasificado, ADR-021 seguimiento)."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    mock_audit.append.assert_called_once()
    kwargs = mock_audit.append.call_args.kwargs
    assert kwargs["action"] == "evento.dead_letter.viewed"
    assert kwargs["object_type"] == "evento"
    assert kwargs["object_ref"] == EVT
    assert kwargs["actor"] == str(USER)
    assert set(kwargs["payload"]) == {"previous_status", "event_type", "origin_stream"}


@pytest.mark.asyncio
async def test_detail_blocked_by_clearance_does_not_write_audit(client_with_conn) -> None:
    """Una vista bloqueada (404 neutro) NO deja rastro en audit_log — de lo
    contrario confirmaría la existencia del evento a un admin sin clearance."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "status": 404},
    )
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(side_effect=denial)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    mock_audit.append.assert_not_called()


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
        patch("app.routers.admin_deadletter.apply_event",
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
        patch("app.routers.admin_deadletter.apply_event",
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
        patch("app.routers.admin_deadletter.apply_event",
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


@pytest.mark.asyncio
async def test_replay_reclassified_obsoleto_se_ancla_al_nivel_vivo(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row(nivel_nuevo=1)  # sobre obsoleto: 1
    conn.execute.return_value = "UPDATE 1"
    conn.fetchval.return_value = 3  # nivel vivo del radicado
    apply = AsyncMock(return_value=None)
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_event", apply),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert apply.call_args.args[3]["nivel_nuevo"] == 3


@pytest.mark.asyncio
async def test_replay_sin_radicado_vivo_da_409_sin_aplicar(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    # permiso OK; radicado vivo inexistente
    conn.fetchval.side_effect = lambda q, *a: None if "FROM radicados" in q else 1
    apply = AsyncMock(return_value=None)
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_event", apply),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "stale_event"
    apply.assert_not_called()


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


# ------------------------------------------- origin_group scoping (shared table)
# `evento_dead_letter` is shared with workflow-service in the same tenant schema
# (see module note in app/repositories/dead_letter_repository.py). These tests
# cover both ends: (a) the router always queries with ITS OWN origin_group
# (wiring), and (b) the repository methods actually filter rows by
# origin_group so a row belonging to the other consumer is invisible here.

@pytest.mark.asyncio
async def test_list_passes_own_origin_group_to_repo(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [_candidate_row()]
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.resolve_deadletter_levels",
              AsyncMock(return_value={EVT: 1})),
    ):
        await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    params = conn.fetch.call_args[0][1:]
    assert DLQ_GROUP in params
    assert OTHER_GROUP not in params


@pytest.mark.asyncio
async def test_detail_passes_own_origin_group_to_repo(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
    ):
        await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    # get_by_event_id(event_id, caller_slug, origin_group)
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_replay_passes_own_origin_group_to_get_for_update(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_event",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    # get_for_update(event_id, caller_slug, origin_group)
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP
    # transition_status(event_id, caller_slug, new_status, origin_group)
    assert conn.execute.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_discard_passes_own_origin_group(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "x"}
        )
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP
    assert conn.execute.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_repo_get_by_event_id_filters_by_origin_group() -> None:
    """Repository-level: a row for the same event_id/tenant but a DIFFERENT
    origin_group (the other consumer's row in the shared table) must not be
    visible — this is the actual filtering behaviour, not just call wiring."""
    table = {
        (EVT, "demo", DLQ_GROUP): {"event_id": EVT, "origin_group": DLQ_GROUP},
        (EVT, "demo", OTHER_GROUP): {"event_id": EVT, "origin_group": OTHER_GROUP},
    }

    async def fake_fetchrow(_sql, event_id, caller_slug, origin_group):
        return table.get((event_id, caller_slug, origin_group))

    conn = AsyncMock()
    conn.fetchrow = fake_fetchrow
    repo = DeadLetterRepository(conn)

    own_row = await repo.get_by_event_id(EVT, "demo", DLQ_GROUP)
    assert own_row is not None and own_row["origin_group"] == DLQ_GROUP

    # Same event_id/tenant, but asking under the OTHER service's group should
    # not leak this service's row, nor return the other service's row from here.
    cross_row = await repo.get_by_event_id(EVT, "demo", OTHER_GROUP)
    assert cross_row is not None and cross_row["origin_group"] == OTHER_GROUP
    assert cross_row != own_row


@pytest.mark.asyncio
async def test_repo_transition_status_does_not_affect_other_origin_group() -> None:
    """transition_status's UPDATE … WHERE origin_group = $N must report 0 rows
    affected when the row id matches but the origin_group belongs to the other
    consumer — replay/discard must not silently resolve someone else's event."""
    conn = AsyncMock()

    async def fake_execute(_sql, event_id, caller_slug, new_status, origin_group):
        if origin_group != DLQ_GROUP:
            return "UPDATE 0"
        return "UPDATE 1"

    conn.execute = fake_execute
    repo = DeadLetterRepository(conn)

    rows_own = await repo.transition_status(EVT, "demo", "resolved", DLQ_GROUP)
    assert rows_own == 1

    rows_other = await repo.transition_status(EVT, "demo", "resolved", OTHER_GROUP)
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
async def test_fail_closed_nivel_max_clearance_passes() -> None:
    """Aun con radicado no hallado, un llamante CLASIFICADA (3) no es bloqueado."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}
    # No debe lanzar.
    await assert_can_read_deadletter(conn, caller_clearance=3, row=row)


# ------------------------------------- resolve_deadletter_level (unit, ADR-021 seguimiento)
@pytest.mark.asyncio
async def test_resolve_deadletter_level_matches_assert_can_read_gate() -> None:
    """Misma fuente de verdad: el nivel que resuelve la función usada por el
    listado es EXACTAMENTE el que usa el gate 404 del detalle (consistencia
    listado↔detalle exigida por el contrato)."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=2)  # nivel_vivo del radicado
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}

    nivel = await resolve_deadletter_level(conn, row)
    assert nivel == 2

    # El gate debe bloquear exactamente cuando clearance < nivel resuelto arriba.
    with pytest.raises(HTTPException):
        await assert_can_read_deadletter(conn, caller_clearance=1, row=row)
    await assert_can_read_deadletter(conn, caller_clearance=2, row=row)  # no lanza


@pytest.mark.asyncio
async def test_resolve_deadletter_level_no_identifiers_fail_closed() -> None:
    conn = AsyncMock()
    row = {"payload": {"payload": {}}}
    nivel = await resolve_deadletter_level(conn, row)
    assert nivel == 3  # CLASIFICADA
    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_deadletter_level_undefined_table_fail_closed() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=asyncpg.UndefinedTableError())
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}
    nivel = await resolve_deadletter_level(conn, row)
    assert nivel == 3  # CLASIFICADA — tabla radicados ausente en el tenant


# --------------------------------------- resolve_deadletter_levels (unit, ADR-021 Baja [2])
#
# El batch debe ser byte-a-byte equivalente al per-row: para toda fila `r`,
# resolve_deadletter_levels([r])[r["event_id"]] == (resolve_deadletter_level(r),
# capturando cualquier excepción como CLASIFICADA). Estos tests cubren las 5
# ramas relevantes con UNA sola consulta simulada en lote (``FakeConn.fetch``)
# y comparan contra el resultado real del per-row (``FakeConn.fetchval``) fila
# a fila, en vez de solo fijar valores esperados a mano.
class _FakeRadicadosConn:
    """Doble mínimo de asyncpg.Connection: sirve tanto al per-row (fetchval,
    una fila por identificador) como al batch (fetch, un único IN/ANY)."""

    def __init__(self, levels_by_ident: dict[str, int]) -> None:
        self._levels = levels_by_ident

    async def fetchval(self, _sql, ident):
        return self._levels.get(ident)

    async def fetch(self, _sql, idents):
        return [
            {"nivel_seguridad": nivel, "tracking_number": ident, "id": f"radicado-{ident}"}
            for ident, nivel in self._levels.items()
            if ident in idents
        ]


@pytest.mark.asyncio
async def test_resolve_deadletter_levels_matches_per_row_for_various_cases() -> None:
    """Contrato de equivalencia con las 5 ramas: hallado, embebido eleva un
    nivel ya hallado, no-hallado, sin-identificador, y payload corrupto."""
    hallado_row = {
        "event_id": "row-hallado",
        "payload": {"payload": {"tracking_number": TRACKING}},
    }
    embebido_row = {
        "event_id": "row-embebido",
        "payload": {
            "payload": {"tracking_number": "2024-DEMO-E-000002", "nivel_nuevo": 3}
        },
    }
    no_hallado_row = {
        "event_id": "row-no-hallado",
        "payload": {"payload": {"tracking_number": "2024-DEMO-E-999999"}},
    }
    sin_id_row = {
        "event_id": "row-sin-id",
        "payload": {"payload": {}},
    }
    corrupto_row = {
        "event_id": "row-corrupto",
        "payload": "not-valid-json{{{",
    }
    rows = [hallado_row, embebido_row, no_hallado_row, sin_id_row, corrupto_row]

    # nivel_vivo por identificador; "2024-DEMO-E-999999" deliberadamente ausente
    # (radicado no hallado).
    conn = _FakeRadicadosConn({TRACKING: 2, "2024-DEMO-E-000002": 1})

    batch_levels = await resolve_deadletter_levels(conn, rows)

    for row in rows:
        try:
            expected = await resolve_deadletter_level(conn, row)
        except Exception:  # noqa: BLE001 — payload corrupto: el per-row lanza, el batch no
            expected = 3  # CLASIFICADA
        assert batch_levels[row["event_id"]] == expected, row["event_id"]

    # Ancla explícita de valores (no solo auto-consistencia con el per-row).
    assert batch_levels["row-hallado"] == 2
    assert batch_levels["row-embebido"] == 3  # max(nivel_vivo=1, nivel_embebido=3)
    assert batch_levels["row-no-hallado"] == 3  # CLASIFICADA
    assert batch_levels["row-sin-id"] == 3  # CLASIFICADA
    assert batch_levels["row-corrupto"] == 3  # CLASIFICADA


@pytest.mark.asyncio
async def test_resolve_deadletter_levels_undefined_table_fail_closed() -> None:
    """Tabla ``radicados`` ausente (UndefinedTableError) en la consulta en lote
    → todas las filas parseables caen a no-hallado → CLASIFICADA, igual que el
    per-row (:func:`test_resolve_deadletter_level_undefined_table_fail_closed`)."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=asyncpg.UndefinedTableError())
    row = {"event_id": EVT, "payload": {"payload": {"tracking_number": TRACKING}}}

    levels = await resolve_deadletter_levels(conn, [row])
    assert levels[EVT] == 3  # CLASIFICADA


@pytest.mark.asyncio
async def test_resolve_deadletter_levels_empty_rows_skips_query() -> None:
    """Sin candidatas, el batch no debe emitir la consulta en lote."""
    conn = AsyncMock()
    levels = await resolve_deadletter_levels(conn, [])
    assert levels == {}
    conn.fetch.assert_not_called()


# ------------------------------------------------ list_scoped_for_clearance (repo unit)
@pytest.mark.asyncio
async def test_repo_list_scoped_for_clearance_includes_payload_no_pagination_params() -> None:
    """A diferencia de list_pending, esta consulta lleva payload (necesario para
    resolver nivel) y NO recibe offset/limit de paginación — solo un tope (cap)."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[_candidate_row()])
    repo = DeadLetterRepository(conn)

    rows = await repo.list_scoped_for_clearance("demo", DLQ_GROUP, status="pending")

    assert rows == [_candidate_row()]
    sql, *params = conn.fetch.call_args[0]
    assert "payload" in sql
    assert "OFFSET" not in sql
    assert params == ["demo", "pending", DLQ_GROUP, 1001]  # cap + 1 (AREA-10-08)  # cap por defecto


@pytest.mark.asyncio
async def test_repo_list_scoped_for_clearance_filters_by_origin_group() -> None:
    """Predicado de origin_group también en la consulta usada por el filtrado de
    clearance — la tabla es compartida con el otro consumidor (ver nota de módulo)."""
    table = {
        ("demo", "pending", DLQ_GROUP): [_candidate_row("own-event")],
        ("demo", "pending", OTHER_GROUP): [_candidate_row("other-event")],
    }

    async def fake_fetch(_sql, caller_slug, status_value, origin_group, _cap):
        return table.get((caller_slug, status_value, origin_group), [])

    conn = AsyncMock()
    conn.fetch = fake_fetch
    repo = DeadLetterRepository(conn)

    own_rows = await repo.list_scoped_for_clearance("demo", DLQ_GROUP, status="pending")
    assert [r["event_id"] for r in own_rows] == ["own-event"]

    other_rows = await repo.list_scoped_for_clearance("demo", OTHER_GROUP, status="pending")
    assert [r["event_id"] for r in other_rows] == ["other-event"]


@pytest.mark.asyncio
async def test_repo_list_scoped_pide_una_fila_de_mas_para_detectar_el_truncado() -> None:
    """AREA-10-08: el LIMIT es cap + 1 (la fila sobrante distingue «hay más» de
    «hay justo cap»)."""
    conn = AsyncMock()
    conn.fetch.return_value = []
    await DeadLetterRepository(conn).list_scoped_for_clearance("demo", "grp")
    assert conn.fetch.call_args[0][-1] == DEADLETTER_CANDIDATE_CAP + 1
