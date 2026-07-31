# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
`to_dept`/`from_dept` validados contra el catálogo `dependencias` (agujero de
disponibilidad, cerrado 2026-07-29/30).

`flow_steps.to_dept`/`from_dept` son texto libre (`VARCHAR(100)`, sin FK a
`dependencias`) — `_create_step` (assign/transfer) nunca lo comprobaba. Como
`_own_scope_condition` (bandeja/pending) correlaciona por NOMBRE exacto contra
`dependencias.nombre`, un `to_dept` con una errata o inventado (posible con
`PERM_TRAMITAR` vía API directa) crea un paso que no coincide con el alcance
propio de NINGÚN usuario: el radicado queda `pending` en BD pero invisible en
toda bandeja — pérdida silenciosa de un documento oficial dentro del flujo.

Repository call sequence para ASSIGN/TRANSFER (ver también test_workflow.py):
  1. fetchval — require_permission(PERM_TRAMITAR)
  2. fetchval — get_radicado_tracking_number
  3. fetchval — dependencia_activa(to_dept)
  4. fetchval — dependencia_activa(from_dept)   [solo transfer, si to_dept pasó]
  5. fetchval — get_next_step_number            [solo si ambas validaciones pasan]
  ...
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import RADICADO_ID, TENANT_HEADERS, TRACKING_NUMBER, make_step_record

_ASSIGN_PAYLOAD = {
    "radicado_id": str(RADICADO_ID),
    "tracking_number": TRACKING_NUMBER,
    "to_dept": "Dependencia Inexistente",
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
# assign — to_dept desconocido / inactivo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_to_dept_desconocido_422_sin_fila(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """`to_dept` sin correspondencia (activa) en `dependencias` → 422
    `dependencia_desconocida`, y NINGUNA fila llega a escribirse en
    `flow_steps` (se corta antes de `get_next_step_number`/la transacción)."""
    client, mock_conn, mock_redis = client_with_conn
    # 1) require_permission → True; 2) get_radicado_tracking_number → OK;
    # 3) dependencia_activa(to_dept) → False (no existe / inactiva)
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, None]

    response = await client.post(
        "/api/v1/workflows/assign",
        json=_ASSIGN_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["error"] == "dependencia_desconocida"
    assert body["detail"]["field"] == "to_dept"
    mock_conn.fetchrow.assert_not_awaited()  # ni INSERT INTO flow_steps
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_assign_to_dept_inactivo_422(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """Una dependencia que SÍ existe en el catálogo pero está `activo = false`
    cuenta como inexistente a propósito: un paso hacia ella es igual de
    invisible en la práctica (nadie tiene URD activa apuntando a ella), y el
    predicado SQL de `dependencia_activa` ya filtra `activo = TRUE` — desde el
    punto de vista del servicio, "no existe" e "inactiva" son indistinguibles
    (mismo `fetchval` → None), y eso es exactamente lo que se quiere: se
    rechaza igual."""
    client, mock_conn, mock_redis = client_with_conn
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, None]

    response = await client.post(
        "/api/v1/workflows/assign",
        json=_ASSIGN_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "dependencia_desconocida"
    mock_conn.fetchrow.assert_not_awaited()
    mock_redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# transfer — to_dept / from_dept desconocidos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_to_dept_desconocido_422_sin_fila(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    payload = {**_TRANSFER_PAYLOAD, "to_dept": "Dependencia Inexistente"}
    # 1) require_permission; 2) get_radicado_tracking_number;
    # 3) dependencia_activa(to_dept) → False (corta antes de mirar from_dept)
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, None]

    response = await client.post(
        "/api/v1/workflows/transfer",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "dependencia_desconocida"
    assert response.json()["detail"]["field"] == "to_dept"
    mock_conn.fetchrow.assert_not_awaited()
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_from_dept_desconocido_422_sin_fila(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """`to_dept` válido pero `from_dept` (también enviado en `transfer`) no
    corresponde a ninguna dependencia activa → 422 sobre `from_dept`."""
    client, mock_conn, mock_redis = client_with_conn
    payload = {**_TRANSFER_PAYLOAD, "from_dept": "Origen Inventado"}
    # 1) require_permission; 2) get_radicado_tracking_number;
    # 3) dependencia_activa(to_dept) → True; 4) dependencia_activa(from_dept) → False
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, 1, None]

    response = await client.post(
        "/api/v1/workflows/transfer",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["error"] == "dependencia_desconocida"
    assert body["detail"]["field"] == "from_dept"
    mock_conn.fetchrow.assert_not_awaited()
    mock_redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# Control positivo: dependencias válidas siguen funcionando
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_dependencia_valida_sigue_funcionando(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    payload = {**_ASSIGN_PAYLOAD, "to_dept": "Dirección Jurídica"}
    # 1) require_permission; 2) get_radicado_tracking_number;
    # 3) dependencia_activa(to_dept) → True; 4) get_next_step_number → 1;
    # 5) audit.append's prev_hash lookup → None
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, 1, 1, None]
    mock_conn.fetchrow.return_value = make_step_record(
        to_dept="Dirección Jurídica", action="assign"
    )

    response = await client.post(
        "/api/v1/workflows/assign",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["to_dept"] == "Dirección Jurídica"
    mock_redis.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_transfer_dependencias_validas_siguen_funcionando(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    # 1) require_permission; 2) get_radicado_tracking_number;
    # 3) dependencia_activa(to_dept) → True; 4) dependencia_activa(from_dept) → True;
    # 5) get_next_step_number → 2; 6) audit.append's prev_hash lookup → None
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, 1, 1, 2, None]
    mock_conn.fetchrow.return_value = make_step_record(
        step_number=2,
        from_dept="Dirección Jurídica",
        to_dept="Secretaría General",
        action="transfer",
    )

    response = await client.post(
        "/api/v1/workflows/transfer",
        json=_TRANSFER_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["from_dept"] == "Dirección Jurídica"
    assert body["to_dept"] == "Secretaría General"
    mock_redis.xadd.assert_awaited_once()
