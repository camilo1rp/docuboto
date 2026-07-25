# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for workflow-service endpoints.

DB and Redis are fully mocked — no real infrastructure required.

Repository call sequence for ASSIGN / TRANSFER:
  1. fetchval  — SET search_path + MAX(step_number) → next step number
  2. execute   — SET search_path + UPDATE completed_at on active step
  3. fetchrow  — SET search_path + INSERT INTO flow_steps RETURNING *

Repository call sequence for GET HISTORY:
  1. execute   — SET search_path (via fetch)
  2. fetch     — SELECT * FROM flow_steps WHERE radicado_id ORDER BY step_number

Repository call sequence for GET CURRENT:
  1. execute   — SET search_path (via fetchrow)
  2. fetchrow  — SELECT active step

Repository call sequence for LIST PENDING:
  1. execute   — SET search_path (via fetchval)
  2. fetchval  — COUNT
  3. execute   — SET search_path (via fetch)
  4. fetch     — SELECT page

Repository call sequence for COMPLETE STEP:
  1. execute   — SET search_path (via fetchrow)
  2. fetchrow  — UPDATE flow_steps RETURNING *
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from tests.conftest import (
    RADICADO_ID,
    STEP_ID,
    TENANT_HEADERS,
    TENANT_SLUG,
    TRACKING_NUMBER,
    USER_ID,
    make_event_record,
    make_step_record,
)

_ASSIGN_PAYLOAD = {
    "radicado_id": str(RADICADO_ID),
    "tracking_number": TRACKING_NUMBER,
    "to_dept": "Dirección Jurídica",
    "assigned_to": str(uuid.uuid4()),
    "action": "assign",
    "notes": "Para concepto jurídico",
}

_TRANSFER_PAYLOAD = {
    "radicado_id": str(RADICADO_ID),
    "tracking_number": TRACKING_NUMBER,
    "from_dept": "Dirección Jurídica",
    "to_dept": "Secretaría General",
    "action": "transfer",
    "notes": "Traslado para firma",
}


# ---------------------------------------------------------------------------
# POST /api/v1/workflows/assign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_radicado(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    record = make_step_record(step_id=STEP_ID, action="assign")
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = record

    response = await client.post(
        "/api/v1/workflows/assign",
        json=_ASSIGN_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(STEP_ID)
    assert body["tracking_number"] == TRACKING_NUMBER
    assert body["action"] == "assign"
    assert body["status"] == "pending"
    mock_redis.xadd.assert_awaited_once()
    args = mock_redis.xadd.call_args[0]
    assert args[0] == "orpycamcp.workflow.events"
    # Sobre canónico (orpycamcp_common): event_type + tenant_slug a nivel superior.
    assert args[1]["event_type"] == "workflow.step.created"
    # Se escribió el evento append-only (RF-FLU-02) junto al paso.
    assert any("flow_events" in str(c.args[0]) for c in mock_conn.execute.await_args_list)
    assert args[1]["tenant_slug"] == TENANT_SLUG


# ---------------------------------------------------------------------------
# POST /api/v1/workflows/assign — autorización (PERM_TRAMITAR) + tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_missing_tenant_header(client: AsyncClient) -> None:
    """Sin X-Tenant-Slug no se puede resolver el RBAC del tenant → 403.

    `assign` es ahora una ruta con `require_permission("PERM_TRAMITAR")`, y esa
    dependency deniega explícitamente (403) cuando falta el tenant — se resuelve
    ANTES que la validación `_require_tenant` que antes daba 400. Mismo
    comportamiento que las demás rutas gateadas (cf. `crear_cadena`).
    """
    response = await client.post(
        "/api/v1/workflows/assign",
        json=_ASSIGN_PAYLOAD,
        headers={"X-User-Id": str(USER_ID)},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_assign_sin_permiso_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Sin PERM_TRAMITAR, assign devuelve 403 (fail-closed).

    `check_permission` usa `fetchval`; `None` = usuario sin el permiso. Antes de
    este cierre, assign no tenía gate alguno: cualquier usuario autenticado del
    tenant podía crear/reasignar el flujo de cualquier radicado (RF-SEG-03).
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission → False
    response = await client.post(
        "/api/v1/workflows/assign",
        json=_ASSIGN_PAYLOAD,
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# POST /api/v1/workflows/transfer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_radicado(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    record = make_step_record(
        step_id=STEP_ID,
        step_number=2,
        from_dept="Dirección Jurídica",
        to_dept="Secretaría General",
        action="transfer",
    )
    mock_conn.fetchval.return_value = 2
    mock_conn.fetchrow.return_value = record

    response = await client.post(
        "/api/v1/workflows/transfer",
        json=_TRANSFER_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["action"] == "transfer"
    assert body["step_number"] == 2
    assert body["from_dept"] == "Dirección Jurídica"
    assert body["to_dept"] == "Secretaría General"
    # complete_active_step must have been called (execute for UPDATE)
    mock_conn.execute.assert_awaited()
    mock_redis.xadd.assert_awaited_once()


# ---------------------------------------------------------------------------
# GET /api/v1/workflows/{radicado_id}/history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_flow_history(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    records = [
        make_step_record(step_number=1, action="assign"),
        make_step_record(step_number=2, action="transfer", step_status="completed"),
    ]
    mock_conn.fetch.return_value = records

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/history",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["radicado_id"] == str(RADICADO_ID)
    assert body["tracking_number"] == TRACKING_NUMBER
    assert len(body["steps"]) == 2
    assert body["steps"][0]["step_number"] == 1
    assert body["steps"][1]["step_number"] == 2


# ---------------------------------------------------------------------------
# GET /api/v1/workflows/{radicado_id}/history — empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_flow_history_empty(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/history",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["steps"] == []
    assert body["tracking_number"] == ""


# ---------------------------------------------------------------------------
# GET /api/v1/workflows/{radicado_id}/current
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_step(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    record = make_step_record(step_id=STEP_ID, step_status="in_progress")
    mock_conn.fetchrow.return_value = record

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/current",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(STEP_ID)
    assert body["status"] == "in_progress"


# ---------------------------------------------------------------------------
# GET /api/v1/workflows/{radicado_id}/current — no active step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_step_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/current",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "no_active_step"


# ---------------------------------------------------------------------------
# GET /api/v1/workflows/pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    records = [
        make_step_record(step_id=uuid.uuid4(), to_dept="Dirección Jurídica"),
        make_step_record(step_id=uuid.uuid4(), to_dept="Dirección Jurídica"),
    ]
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = records

    response = await client.get(
        "/api/v1/workflows/pending",
        params={"dept": "Dirección Jurídica", "page": 1, "size": 20},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "2"
    body = response.json()
    assert len(body) == 2
    assert all(item["to_dept"] == "Dirección Jurídica" for item in body)


# ---------------------------------------------------------------------------
# PATCH /api/v1/workflows/{step_id}/complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_step(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    record = make_step_record(step_id=STEP_ID, step_status="completed")
    mock_conn.fetchrow.return_value = record

    response = await client.patch(
        f"/api/v1/workflows/{STEP_ID}/complete",
        json={"notes": "Procesado"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(STEP_ID)
    assert body["status"] == "completed"
    mock_redis.xadd.assert_awaited_once()
    args = mock_redis.xadd.call_args[0]
    assert args[1]["event_type"] == "workflow.step.completed"


# ---------------------------------------------------------------------------
# PATCH /api/v1/workflows/{step_id}/complete — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_step_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.patch(
        f"/api/v1/workflows/{uuid.uuid4()}/complete",
        json={"notes": "Procesado"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "step_not_found"


# ---------------------------------------------------------------------------
# GET /api/v1/workflows/{radicado_id}/events — historial append-only (RF-FLU-02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_flow_events(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        make_event_record(tipo_tx="assign", detalles={"step_number": 1}),
        make_event_record(tipo_tx="complete", detalles={"step_id": "x"}),
    ]

    response = await client.get(f"/api/v1/workflows/{RADICADO_ID}/events", headers=TENANT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert [e["tipo_tx"] for e in body] == ["assign", "complete"]
    assert body[0]["detalles"]["step_number"] == 1


@pytest.mark.asyncio
async def test_get_flow_events_is_clearance_scoped(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """La hoja de ruta se acota por el clearance del llamante (E08 §9 / RF-SEG-08).

    Canal B: NO requiere permiso de admin, pero la consulta filtra por la visibilidad del
    radicado. Con `fetchrow` a None el clearance es PUBLICA (1) y la consulta de eventos
    incluye la condición de nivel de seguridad parametrizada.
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # usuario sin clearance → PUBLICA (1)
    mock_conn.fetch.return_value = []

    response = await client.get(f"/api/v1/workflows/{RADICADO_ID}/events", headers=TENANT_HEADERS)

    assert response.status_code == 200
    events_sql = mock_conn.fetch.call_args[0][0]
    assert "nivel_seguridad" in events_sql and "<=" in events_sql
    # El clearance (PUBLICA=1) se pasa como parámetro vinculado de la consulta.
    assert mock_conn.fetch.call_args[0][2] == 1


# ---------------------------------------------------------------------------
# GET /api/v1/workflows/inbox — bandejas tipadas (RF-FLU-03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_by_box(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_step_record(doc_type="E")]

    response = await client.get("/api/v1/workflows/inbox?box=entrada", headers=TENANT_HEADERS)
    assert response.status_code == 200
    assert response.headers["x-total-count"] == "1"
    body = response.json()
    assert len(body) == 1
    assert body[0]["doc_type"] == "E"


@pytest.mark.asyncio
async def test_inbox_invalid_box_is_400(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _, _ = client_with_conn
    response = await client.get("/api/v1/workflows/inbox?box=otra", headers=TENANT_HEADERS)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_box"


# ---------------------------------------------------------------------------
# Transacciones de trámite (RF-FLU-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_transaction_types(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        {
            "code": "informar",
            "nombre": "Informar",
            "descripcion": None,
            "requires_permission": "PERM_INFORMAR",
            "target_step_status": None,
            "activo": True,
        },
        {
            "code": "anular",
            "nombre": "Anular",
            "descripcion": None,
            "requires_permission": "PERM_ANULAR",
            "target_step_status": "cancelled",
            "activo": True,
        },
    ]
    resp = await client.get("/api/v1/workflows/transaction-types", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert {t["code"] for t in resp.json()} == {"informar", "anular"}


@pytest.mark.asyncio
async def test_execute_transaction_anular(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    tt = {
        "code": "anular",
        "nombre": "Anular",
        "descripcion": None,
        "requires_permission": "PERM_ANULAR",
        "target_step_status": "cancelled",
        "activo": True,
    }
    latest = make_step_record(step_status="pending", to_dept="Jurídica")
    event = make_event_record(tipo_tx="anular", to_dept="Jurídica")
    # get_transaction_type → tt, get_latest_step → latest, apply_transaction INSERT → event
    mock_conn.fetchrow.side_effect = [tt, latest, event]

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "anular", "comentario": "Duplicado"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["tipo_tx"] == "anular"


@pytest.mark.asyncio
async def test_execute_transaction_sin_permiso_del_tipo_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """El permiso que declara el tipo (`requires_permission`) se hace cumplir aquí.

    Es el hallazgo central de la auditoría E05: la migración 007 delegaba el
    enforcement "al gateway", pero el gateway sólo AUTENTICA. Sin PERM_ANULAR, un
    usuario autenticado no puede ejecutar `anular` — y la transacción NO se aplica
    (no se llega a `get_latest_step` ni al INSERT append-only).
    """
    client, mock_conn, _ = client_with_conn
    tt = {
        "code": "anular",
        "nombre": "Anular",
        "descripcion": None,
        "requires_permission": "PERM_ANULAR",
        "target_step_status": "cancelled",
        "activo": True,
    }
    mock_conn.fetchrow.return_value = tt  # get_transaction_type → tt
    mock_conn.fetchval.return_value = None  # check_permission(PERM_ANULAR) → False

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "anular", "comentario": "Intento sin permiso"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # Fail-closed real: no se ejecutó el INSERT append-only en flow_events.
    assert not any(
        "flow_events" in str(c.args[0]) for c in mock_conn.execute.await_args_list
    ), "la transacción no debe aplicarse cuando el permiso del tipo falta"


@pytest.mark.asyncio
async def test_execute_transaction_tipo_abierto_sin_permiso_ok(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un tipo con `requires_permission = NULL` es abierto: no exige permiso.

    Control positivo del gate: la nulidad de la columna significa "sin permiso",
    no "fail-closed" — se ejecuta aunque el RBAC no conceda nada.
    """
    client, mock_conn, _ = client_with_conn
    tt = {
        "code": "marcar_visto",
        "nombre": "Marcar visto",
        "descripcion": None,
        "requires_permission": None,  # tipo abierto
        "target_step_status": None,
        "activo": True,
    }
    latest = make_step_record(step_status="pending")
    event = make_event_record(tipo_tx="marcar_visto")
    mock_conn.fetchrow.side_effect = [tt, latest, event]
    mock_conn.fetchval.return_value = None  # RBAC no concede nada; da igual, es abierto

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "marcar_visto"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["tipo_tx"] == "marcar_visto"


@pytest.mark.asyncio
async def test_execute_transaction_unknown(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_transaction_type → None

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "inexistente"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_transaction"


@pytest.mark.asyncio
async def test_execute_transaction_no_flow(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    tt = {
        "code": "informar",
        "nombre": "Informar",
        "descripcion": None,
        "requires_permission": "PERM_INFORMAR",
        "target_step_status": None,
        "activo": True,
    }
    # get_transaction_type → tt, get_latest_step → None
    mock_conn.fetchrow.side_effect = [tt, None]

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "informar"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "no_flow"


# ---------------------------------------------------------------------------
# Vencimientos y semáforos (RF-FLU-07)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_semaforo_vencido(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    past = datetime.now(tz=UTC) - timedelta(days=2)
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_step_record(due_at=past)]

    response = await client.get("/api/v1/workflows/inbox", headers=TENANT_HEADERS)
    assert response.status_code == 200
    assert response.json()[0]["semaforo"] == "vencido"


@pytest.mark.asyncio
async def test_scan_overdue_emits_and_marks(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    past = datetime.now(tz=UTC) - timedelta(days=1)
    # list_overdue_unnotified → 1 paso vencido; mark_overdue_notified → execute
    mock_conn.fetch.return_value = [make_step_record(due_at=past)]

    response = await client.post("/api/v1/workflows/overdue/scan", headers=TENANT_HEADERS)
    assert response.status_code == 200
    assert response.json()["alerted"] == 1
    mock_redis.xadd.assert_awaited()
    args = mock_redis.xadd.call_args[0]
    assert args[0] == "orpycamcp.workflow.events"
    assert args[1]["event_type"] == "workflow.step.overdue"


@pytest.mark.asyncio
async def test_scan_overdue_none(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []

    response = await client.post("/api/v1/workflows/overdue/scan", headers=TENANT_HEADERS)
    assert response.status_code == 200
    assert response.json()["alerted"] == 0


# ---------------------------------------------------------------------------
# Rollback + reasignación en cascada (RF-FLU-08)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devolver_al_remitente(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # D-06: assigned_to debe coincidir con el actor (USER_ID) para pasar la verificación de tenencia.
    current = make_step_record(
        step_number=2, step_status="pending", to_dept="Jurídica", assigned_to=USER_ID
    )
    devuelto = make_step_record(step_number=3, step_status="pending", to_dept="Ventanilla")
    mock_conn.fetchrow.side_effect = [current, devuelto]

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/devolver",
        json={"to_dept": "Ventanilla", "causal": "DEV-DEP", "comentario": "No competencia"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["to_dept"] == "Ventanilla"


@pytest.mark.asyncio
async def test_devolver_no_active_step(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/devolver",
        json={"to_dept": "Ventanilla", "causal": "DEV-DEP"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "no_active_step"


@pytest.mark.asyncio
async def test_devolver_not_current_holder_is_neutral_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """D-06/M2 (no-oráculo): un no-tenedor recibe el MISMO 404 `no_active_step`
    que el caso sin paso activo — YA NO un 403 `not_current_holder` distinguible
    (ese 403 permitía sondear existencia/tenencia de un radicado clasificado)."""
    client, mock_conn, _ = client_with_conn
    # El paso pertenece a un usuario distinto del actor (USER_ID).
    other_holder = uuid.uuid4()
    current = make_step_record(
        step_number=1, step_status="pending", to_dept="Jurídica", assigned_to=other_holder
    )
    mock_conn.fetchrow.return_value = current

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/devolver",
        json={"to_dept": "Ventanilla", "causal": "DEV-DEP"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "no_active_step"
    # Mismo cuerpo exacto que "sin paso activo" (no-oráculo: byte-a-byte igual).
    no_step_body = resp.json()
    assert no_step_body["detail"]["detail"] == f"No active flow step found for radicado {RADICADO_ID}"


@pytest.mark.asyncio
async def test_rollback_reactivates_previous(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # D-06/M2: assigned_to debe coincidir con el actor (USER_ID) para pasar la
    # verificación de tenencia (rollback es una acción del tenedor actual).
    current = make_step_record(
        step_number=2, step_status="pending", to_dept="B", assigned_to=USER_ID
    )
    previous = make_step_record(step_number=1, step_status="completed", to_dept="A")
    reactivated = make_step_record(step_number=1, step_status="pending", to_dept="A")
    # get_current, get_previous_step, apply_rollback (UPDATE ... RETURNING)
    mock_conn.fetchrow.side_effect = [current, previous, reactivated]

    response = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback", headers=TENANT_HEADERS
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_rollback_no_active_step(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_current → None

    response = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback", headers=TENANT_HEADERS
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "no_active_step"


@pytest.mark.asyncio
async def test_rollback_by_non_holder_is_neutral_no_active_step(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """M2/no-oráculo: un no-tenedor recibe el MISMO error que "no hay paso activo"
    (NO un 403 distinguible) y la mutación (apply_rollback) NUNCA se invoca."""
    client, mock_conn, _ = client_with_conn
    other_holder = uuid.uuid4()
    current = make_step_record(
        step_number=2, step_status="pending", to_dept="B", assigned_to=other_holder
    )
    mock_conn.fetchrow.return_value = current  # solo se consulta get_current

    response = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback", headers=TENANT_HEADERS
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "no_active_step"
    # Solo UNA llamada a fetchrow (get_current); ni get_previous_step ni
    # apply_rollback (que también usarían fetchrow) se invocaron.
    assert mock_conn.fetchrow.await_count == 1


@pytest.mark.asyncio
async def test_rollback_initial_step_rejected(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    current = make_step_record(step_number=1, step_status="pending", assigned_to=USER_ID)
    mock_conn.fetchrow.side_effect = [current, None]  # sin paso previo

    response = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback", headers=TENANT_HEADERS
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "cannot_rollback_initial"


@pytest.mark.asyncio
async def test_reassign_cascade(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [make_step_record(), make_step_record()]

    response = await client.post(
        "/api/v1/workflows/reassign/cascade",
        json={"from_dept": "Dirección Jurídica", "to_assigned_to": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["reassigned"] == 2


@pytest.mark.asyncio
async def test_reassign_cascade_requires_source(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _, _ = client_with_conn
    response = await client.post(
        "/api/v1/workflows/reassign/cascade",
        json={"to_assigned_to": str(uuid.uuid4())},  # sin from_*
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
