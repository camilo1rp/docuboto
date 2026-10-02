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
  1. fetchval  — SET search_path + tracking_number lookup on `radicados`
  2. fetchval  — SET search_path + dependencia_activa(to_dept) [+ from_dept for transfer]
  3. fetchval  — SET search_path + MAX(step_number) → next step number
  4. execute   — SET search_path + UPDATE completed_at on active step
  5. fetchrow  — SET search_path + INSERT INTO flow_steps RETURNING *

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

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.repositories.workflow_repository import _holder_condition, _nivel_poseedor_condition
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
# POST /api/v1/workflows/assign — Bloqueante 3 (radicado_id inventado /
# tracking_number falsificado)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_radicado_inexistente_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """`radicado_id` sin fila en `radicados` → 404 `radicado_not_found`, y no
    queda ninguna fila creada en `flow_steps` (se corta antes de la tx)."""
    client, mock_conn, mock_redis = client_with_conn
    # 1) require_permission (PERM_TRAMITAR) → True; 2) get_radicado_tracking_number → None
    mock_conn.fetchval.side_effect = [1, None]

    response = await client.post(
        "/api/v1/workflows/assign",
        json=_ASSIGN_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "radicado_not_found"
    mock_conn.fetchrow.assert_not_awaited()  # ni INSERT INTO flow_steps
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_assign_persiste_tracking_number_de_bd_no_del_cuerpo(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un `tracking_number` en el cuerpo que NO corresponde al `radicado_id`
    (p. ej. el de otro radicado) se ignora: se persiste el resuelto desde
    `radicados`, tanto en el `INSERT` de `flow_steps` como en `audit_log.object_ref`."""
    client, mock_conn, _ = client_with_conn
    real_tracking = "2024-DEMO-E-999999"
    # 1) require_permission → True; 2) get_radicado_tracking_number → real_tracking;
    # 3) dependencia_activa(to_dept) → True; 4) get_next_step_number → 1;
    # 5) audit.append's prev_hash lookup → None
    mock_conn.fetchval.side_effect = [1, real_tracking, 1, 1, None]
    mock_conn.fetchrow.return_value = make_step_record(
        step_id=STEP_ID, tracking_number=real_tracking, action="assign"
    )

    payload = {**_ASSIGN_PAYLOAD, "tracking_number": "TRACKING-AJENO-FALSIFICADO"}
    response = await client.post(
        "/api/v1/workflows/assign",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201

    insert_step_calls = [
        c for c in mock_conn.fetchrow.await_args_list if "INSERT INTO flow_steps" in c.args[0]
    ]
    assert len(insert_step_calls) == 1
    assert "TRACKING-AJENO-FALSIFICADO" not in insert_step_calls[0].args
    assert real_tracking in insert_step_calls[0].args

    audit_calls = [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[7] == real_tracking  # object_ref


@pytest.mark.asyncio
async def test_assign_action_falsificado_se_ignora(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Hallazgo A: `action` en el cuerpo se sobrescribe SIEMPRE a "assign" en
    este endpoint — un cliente no puede escribir "return"/"transfer" en la
    traza inmutable vía `/assign`."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = make_step_record(step_id=STEP_ID, action="assign")

    payload = {**_ASSIGN_PAYLOAD, "action": "return"}
    response = await client.post(
        "/api/v1/workflows/assign",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    insert_step_calls = [
        c for c in mock_conn.fetchrow.await_args_list if "INSERT INTO flow_steps" in c.args[0]
    ]
    assert "assign" in insert_step_calls[0].args
    assert "return" not in insert_step_calls[0].args
    audit_calls = [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls[0].args[5] == "workflow.assign"  # action


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


@pytest.mark.asyncio
async def test_transfer_action_falsificado_se_ignora(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Hallazgo A gemelo de `test_assign_action_falsificado_se_ignora`, en
    `/transfer`: con `action` falsificado se sorteaba además el 422
    `missing_from_dept`, porque `WorkflowService.transfer` valida `from_dept`
    ANTES de llegar a `_create_step` — la sobrescritura ocurre en el router,
    previa a esa validación, así que no cambia."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 2
    mock_conn.fetchrow.return_value = make_step_record(
        step_id=STEP_ID, step_number=2, action="transfer"
    )

    payload = {**_TRANSFER_PAYLOAD, "action": "return"}
    response = await client.post(
        "/api/v1/workflows/transfer",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    insert_step_calls = [
        c for c in mock_conn.fetchrow.await_args_list if "INSERT INTO flow_steps" in c.args[0]
    ]
    assert "transfer" in insert_step_calls[0].args
    assert "return" not in insert_step_calls[0].args


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
    """Control positivo: cerrar el paso propio (assigned_to == actor) sigue
    funcionando tras anclar la autorización en el `UPDATE` (Bloqueantes 1+2)."""
    client, mock_conn, mock_redis = client_with_conn
    record = make_step_record(step_id=STEP_ID, step_status="completed", assigned_to=USER_ID)
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

    # El `UPDATE` ancla la autorización por tenencia en el MISMO statement —sin
    # ventana TOCTOU— con la ÚNICA definición de poseedor del servicio
    # (`_holder_condition`) y solo sobre un paso ACTIVO.
    update_calls = [
        c for c in mock_conn.fetchrow.await_args_list if "UPDATE flow_steps" in c.args[0]
    ]
    assert len(update_calls) == 1
    sql = update_calls[0].args[0]
    assert _holder_condition(4) in sql
    assert "status IN ('pending', 'in_progress')" in sql
    assert update_calls[0].args[-2] == USER_ID  # actor real, no assigned_to
    # Y el no-read-up del actor viaja en el MISMO `UPDATE` ([A], RF-SEG-08).
    assert _nivel_poseedor_condition(5) in sql
    assert update_calls[0].args[-1] == 3  # clearance resuelto (autouse: 3)

    # La traza forense (Canal A) asienta el actor REAL, no `assigned_to` (Bloqueante 2).
    audit_calls = [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[3] == str(USER_ID)  # posición del actor en _INSERT_SQL

    # ...y el evento operativo append-only (Canal B) también.
    flow_event_calls = [
        c for c in mock_conn.execute.await_args_list if "INSERT INTO flow_events" in c.args[0]
    ]
    assert len(flow_event_calls) == 1
    assert flow_event_calls[0].args[6] == USER_ID  # actor en _insert_flow_event


@pytest.mark.asyncio
async def test_complete_step_paso_ajeno_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Cerrar el paso de OTRA dependencia (el `WHERE` no casa `assigned_to`) es
    byte-a-byte indistinguible de "no existe": mismo 404 `step_not_found`
    (no-oráculo, gemelo de rollback/devolver)."""
    client, mock_conn, mock_redis = client_with_conn
    # 1) `get_by_id` (resuelve el radicado para el cerrojo, ADR-028) -> el paso
    # existe; 2) el `UPDATE` anclado a la tenencia no matchea -> None.
    mock_conn.fetchrow.side_effect = [make_step_record(step_id=STEP_ID), None]

    response = await client.patch(
        f"/api/v1/workflows/{STEP_ID}/complete",
        json={"notes": "Procesado"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "step_not_found"
    # El paso no cambia: ni el evento append-only ni la traza forense se escriben.
    mock_redis.xadd.assert_not_awaited()
    assert not any(
        "INSERT INTO flow_events" in c.args[0] for c in mock_conn.execute.await_args_list
    )
    assert not any(
        "INSERT INTO public.audit_log" in c.args[0] for c in mock_conn.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_complete_step_dependencia_sin_dueno_ok(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un paso de dependencia SIN dueño (`assigned_to IS NULL`, generado por
    `apply_devolucion`) sigue siendo cerrable por quien tramita: no se rompe
    ese caso legítimo al anclar la autorización en el UPDATE."""
    client, mock_conn, mock_redis = client_with_conn
    record = make_step_record(step_id=STEP_ID, step_status="completed")
    record["assigned_to"] = None  # make_step_record's `or` no deja pasar None directo
    mock_conn.fetchrow.return_value = record

    response = await client.patch(
        f"/api/v1/workflows/{STEP_ID}/complete",
        json={"notes": "Procesado"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    mock_redis.xadd.assert_awaited_once()
    update_calls = [
        c for c in mock_conn.fetchrow.await_args_list if "UPDATE flow_steps" in c.args[0]
    ]
    assert "assigned_to IS NULL" in update_calls[0].args[0]


@pytest.mark.asyncio
async def test_complete_step_sin_user_header_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """`PATCH /complete` ahora exige identidad (Bloqueante 1): sin X-User-Id la
    petición ni siquiera resuelve el RBAC de `PERM_TRAMITAR` con un actor real."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission(user_id=None) → False
    response = await client.patch(
        f"/api/v1/workflows/{STEP_ID}/complete",
        json={"notes": "Procesado"},
        headers={"X-Tenant-Slug": TENANT_SLUG},  # sin X-User-Id
    )
    assert response.status_code == 403


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
        "requires_holder": False,
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
        "requires_holder": False,
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
async def test_execute_transaction_tipo_abierto_cae_al_suelo_de_tramitar(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un tipo con `requires_permission = NULL` YA NO es abierto: exige el suelo.

    **Este test afirmaba lo contrario**, y decía «la nulidad de la columna
    significa sin permiso, no fail-closed». Era una decisión escrita, no un
    olvido — por eso se cambia declarándolo y no borrándolo.

    Por qué se cambia: `transaction_types` es un catálogo del tenant, así que
    una fila con la columna vacía —sembrada por una migración futura, o por
    alguien con acceso a la base— quedaba ejecutable por cualquier autenticado
    de la entidad, mientras `assign`, `transfer` y `complete` exigen
    `PERM_TRAMITAR` sobre el mismo radicado. Dejar un campo en blanco no puede
    ser la manera más fácil de abrir una puerta.

    No cambia nada observable hoy: los 12 tipos sembrados declaran su permiso.
    El suelo es `PERM_TRAMITAR` y no un gate de router porque el permiso aquí es
    POR TIPO — exigirlo a todas rompería a quien tiene `PERM_VOBO` para dar
    vistos buenos y nada más.
    """
    client, mock_conn, _ = client_with_conn
    tt = {
        "code": "marcar_visto",
        "nombre": "Marcar visto",
        "descripcion": None,
        "requires_permission": None,  # tipo abierto
        "target_step_status": None,
        "requires_holder": False,
        "activo": True,
    }
    latest = make_step_record(step_status="pending")
    event = make_event_record(tipo_tx="marcar_visto")
    mock_conn.fetchrow.side_effect = [tt, latest, event]
    mock_conn.fetchval.return_value = None  # el RBAC no concede nada

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "marcar_visto"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403

    # Y el control positivo del suelo: con `PERM_TRAMITAR` concedido, el mismo
    # tipo sin permiso propio se ejecuta. Sin esta mitad, el test pasaría igual
    # con un `raise 403` incondicional.
    mock_conn.fetchrow.side_effect = [tt, latest, event]
    mock_conn.fetchval.return_value = True
    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "marcar_visto"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["tipo_tx"] == "marcar_visto"


@pytest.mark.asyncio
async def test_execute_transaction_deja_traza_forense_en_audit_log(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Cierre de deuda declarada 2026-07-30: `execute_transaction` ahora deja
    asiento en `public.audit_log` (Canal A), gemelo de `flow_events` (Canal B) que
    ya dejaba `apply_transaction`. `object_ref` = `tracking_number` de `latest`
    (resuelto server-side por `_create_step`/`_row_to_model`, nunca del cuerpo)."""
    client, mock_conn, _ = client_with_conn
    tt = {
        "code": "anular",
        "nombre": "Anular",
        "descripcion": None,
        "requires_permission": "PERM_ANULAR",
        "target_step_status": "cancelled",
        "requires_holder": False,
        "activo": True,
    }
    latest = make_step_record(step_status="pending", to_dept="Jurídica")
    event = make_event_record(tipo_tx="anular", to_dept="Jurídica")
    mock_conn.fetchrow.side_effect = [tt, latest, event]

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "anular", "comentario": "Duplicado"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200

    audit_calls = [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call.args[3] == str(USER_ID)  # actor
    assert call.args[5] == "workflow.transaccion"  # action
    assert call.args[6] == "radicado"  # object_type
    assert call.args[7] == TRACKING_NUMBER  # object_ref
    payload = json.loads(call.args[8])
    assert payload == {
        "tipo_tx": "anular",
        "comentario": "Duplicado",
        "target_step_status": "cancelled",
        "requires_permission": "PERM_ANULAR",
    }


@pytest.mark.asyncio
async def test_execute_transaction_audit_failure_aborta_la_transaccion(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Atomicidad: si `audit.append` falla, la transacción de trámite entera
    revierte (mismo criterio que `rollback`/`complete_step`/`devolver`)."""
    client, mock_conn, _ = client_with_conn
    tt = {
        "code": "informar",
        "nombre": "Informar",
        "descripcion": None,
        "requires_permission": None,
        "target_step_status": None,
        "requires_holder": False,
        "activo": True,
    }
    latest = make_step_record(step_status="pending")
    event = make_event_record(tipo_tx="informar")
    mock_conn.fetchrow.side_effect = [tt, latest, event]

    # `AsyncClient(transport=ASGITransport(...))` re-lanza las excepciones no
    # manejadas por la app (no las convierte en un 500 HTTP) — se comprueba que
    # la excepción se propaga hasta aquí, es decir, que ninguna respuesta 200
    # llega al cliente confirmando una transacción de trámite sin traza forense.
    with (
        patch(
            "app.services.workflow_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("audit_log no disponible")),
        ),
        pytest.raises(RuntimeError, match="audit_log no disponible"),
    ):
        await client.post(
            f"/api/v1/workflows/{RADICADO_ID}/transactions",
            json={"tipo_tx": "informar"},
            headers=TENANT_HEADERS,
        )


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
        "requires_holder": False,
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
async def test_devolver_deja_traza_forense_en_audit_log(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Cierre de deuda declarada 2026-07-30: `devolver` ahora deja asiento en
    `public.audit_log` (Canal A) además del `flow_events.comentario` (Canal B) que
    ya dejaba. `object_ref` = el `tracking_number` del paso activo (resuelto
    server-side, no del cuerpo)."""
    client, mock_conn, _ = client_with_conn
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

    audit_calls = [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call.args[3] == str(USER_ID)  # actor
    assert call.args[5] == "workflow.devolver"  # action
    assert call.args[6] == "radicado"  # object_type
    assert call.args[7] == TRACKING_NUMBER  # object_ref (tracking_number del paso activo)
    payload = json.loads(call.args[8])
    assert payload == {
        "from_dept": "Jurídica",
        "to_dept": "Ventanilla",
        "causal": "DEV-DEP",
        "comentario": "No competencia",
    }


@pytest.mark.asyncio
async def test_devolver_audit_failure_aborta_la_devolucion(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Atomicidad (mismo criterio que `rollback`/`_create_step`): si `audit.append`
    falla, la devolución entera revierte — no debe llegar a la respuesta 200 que
    confirmaría al cliente un cambio de dependencia sin traza forense."""
    client, mock_conn, _ = client_with_conn
    current = make_step_record(
        step_number=2, step_status="pending", to_dept="Jurídica", assigned_to=USER_ID
    )
    devuelto = make_step_record(step_number=3, step_status="pending", to_dept="Ventanilla")
    mock_conn.fetchrow.side_effect = [current, devuelto]

    with (
        patch(
            "app.services.workflow_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("audit_log no disponible")),
        ),
        pytest.raises(RuntimeError, match="audit_log no disponible"),
    ):
        await client.post(
            f"/api/v1/workflows/{RADICADO_ID}/devolver",
            json={"to_dept": "Ventanilla", "causal": "DEV-DEP"},
            headers=TENANT_HEADERS,
        )


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

    # `es_poseedor` (una sola definición de poseedor, `_holder_condition`) dice que no.
    with patch(
        "app.services.workflow_service.WorkflowRepository.es_poseedor",
        new=AsyncMock(return_value=False),
    ):
        resp = await client.post(
            f"/api/v1/workflows/{RADICADO_ID}/devolver",
            json={"to_dept": "Ventanilla", "causal": "DEV-DEP"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "no_active_step"
    # Mismo cuerpo exacto que "sin paso activo" (no-oráculo: byte-a-byte igual).
    no_step_body = resp.json()
    assert no_step_body["detail"]["detail"] == "El radicado no tiene ningún paso de flujo activo"


ROLLBACK_MOTIVO = "Se asignó a la dependencia incorrecta por error de digitación."


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
        f"/api/v1/workflows/{RADICADO_ID}/rollback",
        json={"motivo": ROLLBACK_MOTIVO},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    # El motivo queda en el asiento operativo (flow_events.comentario, Canal B —
    # hoja de ruta) vía `execute`, no solo en el 200 de la respuesta.
    flow_event_calls = [
        c for c in mock_conn.execute.await_args_list if "INSERT INTO flow_events" in c.args[0]
    ]
    assert len(flow_event_calls) == 1
    assert ROLLBACK_MOTIVO in flow_event_calls[0].args

    # ...y en la traza legal forense (public.audit_log.payload, Canal A) —
    # atómica con la mutación (mismo `conn.transaction()` que `_create_step`).
    audit_calls = [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    payload = json.loads(audit_calls[0].args[8])  # posición del payload jsonb en _INSERT_SQL
    assert payload["motivo"] == ROLLBACK_MOTIVO


@pytest.mark.asyncio
async def test_rollback_no_active_step(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_current → None

    response = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback",
        json={"motivo": ROLLBACK_MOTIVO},
        headers=TENANT_HEADERS,
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
    mock_conn.fetchrow.return_value = current  # solo se consulta el paso activo

    with patch(
        "app.services.workflow_service.WorkflowRepository.es_poseedor",
        new=AsyncMock(return_value=False),
    ):
        response = await client.post(
            f"/api/v1/workflows/{RADICADO_ID}/rollback",
            json={"motivo": ROLLBACK_MOTIVO},
            headers=TENANT_HEADERS,
        )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "no_active_step"
    # Solo UNA llamada a fetchrow (paso activo); ni get_previous_step ni
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
        f"/api/v1/workflows/{RADICADO_ID}/rollback",
        json={"motivo": ROLLBACK_MOTIVO},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "cannot_rollback_initial"


@pytest.mark.asyncio
async def test_rollback_without_motivo_is_rejected(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """El motivo es obligatorio (espejo del modal `RevertirModal.svelte`, que ya
    lo exige): sin él, la petición no llega al servicio — 422, y no se ejecuta
    ninguna consulta ni mutación **de negocio** contra la BD.

    La aserción decía «ninguna consulta», sin matizar, y dejó de ser cierta al
    gatearse esta ruta con `PERM_TRAMITAR`: la comprobación de autorización sí
    toca la conexión (fija el `search_path` y consulta el RBAC) antes de que
    Pydantic rechace el cuerpo. Eso es lo correcto —autorizar primero— así que
    se ajusta la aserción a lo que el test quiere fijar de verdad: que el
    `rollback` no ocurre. `fetchrow` sigue sin awaitarse, y por ahí pasa toda la
    lectura del paso a revertir."""
    client, mock_conn, _ = client_with_conn

    response = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback",
        json={},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 422
    mock_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_with_too_short_motivo_is_rejected(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Espejo del mínimo de 5 caracteres exigido por el modal del frontend."""
    client, mock_conn, _ = client_with_conn

    response = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback",
        json={"motivo": "abc"},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 422
    mock_conn.fetchrow.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_reassign_cascade_destino_inexistente_422(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Hallazgo C: `to_assigned_to` que no existe en `auth_users` → 422
    fail-closed, y NINGUNA fila de `flow_steps` se toca (el `SELECT`/`UPDATE`
    de `reassign_cascade` en el repo ni se ejecutan)."""
    client, mock_conn, _ = client_with_conn
    # 1) require_permission (USUA_PERM_ADMIN) → True; 2) user_exists → False
    mock_conn.fetchval.side_effect = [1, None]

    response = await client.post(
        "/api/v1/workflows/reassign/cascade",
        json={"from_dept": "Dirección Jurídica", "to_assigned_to": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "to_assigned_to_not_found"
    mock_conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_reassign_cascade_deja_traza_forense_en_audit_log(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Cierre de deuda declarada 2026-07-30: `reassign_cascade` ahora deja
    asiento en `public.audit_log` (Canal A). Es un barrido transversal (sin UN
    radicado único): `object_type="flow_steps"`, `object_ref=to_assigned_to`
    (el único identificador estable no-opcional del payload), y el `payload`
    lleva el conteo REAL de filas reasignadas."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [make_step_record(), make_step_record()]
    to_assigned_to = uuid.uuid4()

    response = await client.post(
        "/api/v1/workflows/reassign/cascade",
        json={"from_dept": "Dirección Jurídica", "to_assigned_to": str(to_assigned_to)},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["reassigned"] == 2

    audit_calls = [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call.args[3] == str(USER_ID)  # actor
    assert call.args[5] == "workflow.reasignacion_cascada"  # action
    assert call.args[6] == "flow_steps"  # object_type
    assert call.args[7] == str(to_assigned_to)  # object_ref
    payload = json.loads(call.args[8])
    assert payload == {
        "from_assigned_to": None,
        "from_dept": "Dirección Jurídica",
        "to_assigned_to": str(to_assigned_to),
        "reassigned": 2,
        "skipped_terminal": 0,
    }


@pytest.mark.asyncio
async def test_reassign_cascade_audit_failure_aborta_la_reasignacion(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Atomicidad: si `audit.append` falla, ninguna reasignación queda
    persistida (mismo criterio que `rollback`/`devolver`/`execute_transaction`)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [make_step_record(), make_step_record()]

    with (
        patch(
            "app.services.workflow_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("audit_log no disponible")),
        ),
        pytest.raises(RuntimeError, match="audit_log no disponible"),
    ):
        await client.post(
            "/api/v1/workflows/reassign/cascade",
            json={"from_dept": "Dirección Jurídica", "to_assigned_to": str(uuid.uuid4())},
            headers=TENANT_HEADERS,
        )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """`/health` es VIVACIDAD: gobierna los reinicios del contenedor.

    Antes tomaba una conexión del pool en cada sondeo (cada 15 s) para preguntar
    `SELECT 1`, metiendo una dependencia externa en la sonda que decide si el
    contenedor se reinicia. Reiniciar por un Postgres caído no arregla Postgres.
    El diagnóstico de dependencias vive ahora en `/health/ready`.
    """
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "workflow-service"}


# ---------------------------------------------------------------------------
# Los dos gates que faltaban: devolver y rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devolver_exige_permiso_de_tramite(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Eran las DOS únicas escrituras de trámite del router sin ningún gate.

    Sus tres hermanas —`assign`, `transfer` y `complete`— exigen
    `PERM_TRAMITAR` sobre el mismo radicado: mismo fichero, mismo objeto, solo
    algunas gateadas. Eso es omisión, no diseño, y el control positivo son ellas.

    Devolver saca el radicado de la dependencia que lo tramita y lo manda al
    origen con una causal. Cualquier autenticado de la entidad podía hacerlo.
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # el RBAC no concede nada

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/devolver",
        json={"to_dept": "100", "causal": "No corresponde a esta dependencia"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_exige_permiso_de_tramite(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Revertir la última asignación deshace el trabajo de otra persona."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/rollback",
        json={"motivo": "Asignada por error"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_awaited()


# --- OMI-01: los eventos que salen declaran el nivel del radicado -------------


@pytest.mark.asyncio
async def test_los_eventos_de_workflow_declaran_nivel_seguridad() -> None:
    """Sin `nivel_seguridad` el webhook no puede decidir si tracking y ruta de
    dependencias de un reservado salen de la entidad (notification los trata como
    clasificados, pero el productor debe declararlo para no recortar de más)."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.services.workflow_service import WorkflowService

    svc = WorkflowService(AsyncMock(), "demo")
    step = SimpleNamespace(
        id=uuid.uuid4(),
        radicado_id=uuid.uuid4(),
        tracking_number="2026-DEMO-E-000001",
        step_number=1,
        from_dept="A",
        to_dept="B",
        action="assign",
        status="pending",
        assigned_by=None,
    )
    emit = AsyncMock()
    with (
        patch("app.services.workflow_service.events.emit", emit),
        patch("app.services.workflow_service.get_redis", return_value=MagicMock()),
        patch.object(svc._repo, "nivel_radicado", new=AsyncMock(return_value=3)),
    ):
        await svc._publish("workflow.step.created", step)
    assert emit.await_count == 1, "no se emitió: la prueba no mide el sobre"
    assert emit.await_args.kwargs["payload"]["nivel_seguridad"] == 3

    # Sin nivel resoluble el campo se omite (notification lo trata como clasificado).
    emit.reset_mock()
    with (
        patch("app.services.workflow_service.events.emit", emit),
        patch("app.services.workflow_service.get_redis", return_value=MagicMock()),
        patch.object(svc._repo, "nivel_radicado", new=AsyncMock(return_value=None)),
    ):
        await svc._publish("workflow.step.created", step)
    assert "nivel_seguridad" not in emit.await_args.kwargs["payload"]


@pytest.mark.asyncio
async def test_estado_cambiado_y_overdue_declaran_nivel_seguridad() -> None:
    """Los otros dos productores (`_emitir_cambios`, `scan_overdue`) también llevan el nivel."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.services.workflow_service import WorkflowService

    svc = WorkflowService(AsyncMock(), "demo")
    rid = uuid.uuid4()
    emit = AsyncMock()
    svc._cambios = [
        SimpleNamespace(
            radicado_id=rid, tracking_number="T", antes="a", despues="b", flow_event_id=uuid.uuid4()
        )
    ]
    overdue = SimpleNamespace(
        id=uuid.uuid4(), radicado_id=rid, tracking_number="T", to_dept="B",
        assigned_to=None, due_at=None,
    )
    with (
        patch("app.services.workflow_service.events.emit", emit),
        patch("app.services.workflow_service.get_redis", return_value=MagicMock()),
        patch.object(svc._repo, "nivel_radicado", new=AsyncMock(return_value=2)),
        patch.object(svc._repo, "list_overdue_unnotified", new=AsyncMock(return_value=[overdue])),
        patch.object(svc._repo, "mark_overdue_notified", new=AsyncMock()),
    ):
        await svc._emitir_cambios(None)
        await svc.scan_overdue()
    tipos = [c.args[2] for c in emit.await_args_list]
    assert tipos == ["workflow.radicado.estado_cambiado", "workflow.step.overdue"]
    assert [c.kwargs["payload"]["nivel_seguridad"] for c in emit.await_args_list] == [2, 2]
