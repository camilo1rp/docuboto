# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
La dependencia se correlaciona por CÓDIGO (B5, ADR-032): contrato del destino/origen.

`flow_steps.to_dept_code` es la llave (bandeja, tenencia, ubicación); `to_dept` es la
instantánea del NOMBRE y la pone el CATÁLOGO. El nombre del cliente sobrevive como
ALIAS deprecado, resuelto con el criterio único de tenant-service
(`resolver_codigo_dependencia`): ambiguo, desconocido y discordante son 422.

Repository call sequence para ASSIGN/TRANSFER (ver también test_workflow.py):
  1. fetchval — require_permission(PERM_TRAMITAR)
  2. fetchval — get_radicado_tracking_number
  3. fetchrow — resolver_alias(nombre)                [solo con el alias por nombre]
  4. fetchval — dependencia_activa(codigo) -> nombre del catálogo (None = 422)
  5. fetchval — get_next_step_number                  [solo si el destino pasó]
  ...
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.flow import FlowStep
from tests.conftest import RADICADO_ID, TENANT_HEADERS, TRACKING_NUMBER, make_step_record

_ASSIGN_PAYLOAD = {
    "radicado_id": str(RADICADO_ID),
    "tracking_number": TRACKING_NUMBER,
    "to_dept_code": 999,
    "assigned_to": str(uuid.uuid4()),
    "action": "assign",
    "notes": "Para concepto jurídico",
}

_TRANSFER_PAYLOAD = {
    "radicado_id": str(RADICADO_ID),
    "tracking_number": TRACKING_NUMBER,
    "to_dept_code": 5,
    "action": "transfer",
    "notes": "Traslado para firma",
}


def _activo(*, codigo: int = 4, nombre: str = "Dirección Jurídica") -> FlowStep:
    r = make_step_record(to_dept=nombre, to_dept_code=codigo, step_status="in_progress")
    return FlowStep(**{k: r[k] for k in FlowStep.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Destino por código desconocido / inactivo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_codigo_desconocido_422_sin_fila(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """Un código sin dependencia ACTIVA → 422 `dependencia_desconocida`, y NINGUNA fila
    llega a escribirse en `flow_steps` (se corta antes de `get_next_step_number`)."""
    client, mock_conn, mock_redis = client_with_conn
    # 1) require_permission; 2) get_radicado_tracking_number; 3) dependencia_activa → None
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, None]

    response = await client.post(
        "/api/v1/workflows/assign", json=_ASSIGN_PAYLOAD, headers=TENANT_HEADERS
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["error"] == "dependencia_desconocida"
    assert body["detail"]["field"] == "to_dept"
    mock_conn.fetchrow.assert_not_awaited()  # ni INSERT INTO flow_steps
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_assign_codigo_inactivo_se_trata_como_inexistente(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """`dependencia_activa` filtra `activo = TRUE`: inactiva e inexistente son lo mismo
    para el servicio (nadie tiene URD activa en ella, el paso quedaría invisible)."""
    client, mock_conn, mock_redis = client_with_conn
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, None]
    response = await client.post(
        "/api/v1/workflows/assign", json=_ASSIGN_PAYLOAD, headers=TENANT_HEADERS
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "dependencia_desconocida"
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_codigo_desconocido_422_sin_fila(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, None]
    response = await client.post(
        "/api/v1/workflows/transfer", json=_TRANSFER_PAYLOAD, headers=TENANT_HEADERS
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "dependencia_desconocida"
    mock_conn.fetchrow.assert_not_awaited()
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_destino_422_de_esquema(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, _, _ = client_with_conn
    payload = {k: v for k, v in _ASSIGN_PAYLOAD.items() if k != "to_dept_code"}
    response = await client.post("/api/v1/workflows/assign", json=payload, headers=TENANT_HEADERS)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Control positivo: el código manda y el nombre guardado es el del CATÁLOGO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_por_codigo_guarda_el_nombre_del_catalogo_y_no_marca_deprecacion(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    # 1) require_permission; 2) tracking; 3) dependencia_activa → nombre del catálogo;
    # 4) get_next_step_number; 5) prev_hash de la auditoría
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, "Dirección Jurídica", 1, None]
    mock_conn.fetchrow.return_value = make_step_record(to_dept="Dirección Jurídica", action="assign")

    response = await client.post(
        "/api/v1/workflows/assign",
        json={**_ASSIGN_PAYLOAD, "to_dept_code": 4},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert "deprecation" not in response.headers
    insert = next(c for c in mock_conn.fetchrow.await_args_list if "INSERT INTO flow_steps" in c.args[0])
    assert "Dirección Jurídica" in insert.args and 4 in insert.args
    body = response.json()
    assert (body["to_dept"], body["to_dept_code"]) == ("Dirección Jurídica", 4)
    mock_redis.xadd.assert_awaited_once()


# ---------------------------------------------------------------------------
# El ALIAS por nombre: único, ambiguo, desconocido, discordante
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alias_por_nombre_unico_se_resuelve_y_deja_cabecera_deprecation(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, _ = client_with_conn
    payload = {k: v for k, v in _ASSIGN_PAYLOAD.items() if k != "to_dept_code"}
    payload["to_dept"] = "Dirección Jurídica"
    # 1) permiso; 2) tracking; 3) dependencia_activa(4); 4) next step; 5) prev_hash
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, "Dirección Jurídica", 1, None]
    mock_conn.fetchrow.side_effect = [
        {"codigo": 4, "candidatos": 1},  # resolver_codigo_dependencia(nombre, false)
        make_step_record(to_dept="Dirección Jurídica", action="assign"),
    ]

    response = await client.post("/api/v1/workflows/assign", json=payload, headers=TENANT_HEADERS)

    assert response.status_code == 201
    assert response.headers["deprecation"] == "true"
    assert 'rel="deprecation"' in response.headers["link"]
    sql, nombre, historico = mock_conn.fetchrow.await_args_list[0].args
    assert "resolver_codigo_dependencia" in sql and nombre == "Dirección Jurídica"
    assert historico is False  # una ACCIÓN no revive nombres anteriores


@pytest.mark.asyncio
async def test_alias_ambiguo_422_y_no_se_escribe_nada(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """Dos dependencias con ese nombre: NO se elige la de menor código."""
    client, mock_conn, mock_redis = client_with_conn
    payload = {k: v for k, v in _ASSIGN_PAYLOAD.items() if k != "to_dept_code"}
    payload["to_dept"] = "Archivo"
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER]
    mock_conn.fetchrow.return_value = {"codigo": None, "candidatos": 2}

    response = await client.post("/api/v1/workflows/assign", json=payload, headers=TENANT_HEADERS)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "dependencia_ambigua" and detail["candidatos"] == 2
    assert not any("INSERT INTO flow_steps" in c.args[0] for c in mock_conn.fetchrow.await_args_list)
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_alias_desconocido_422(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, _ = client_with_conn
    payload = {k: v for k, v in _ASSIGN_PAYLOAD.items() if k != "to_dept_code"}
    payload["to_dept"] = "Nadie"
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER]
    mock_conn.fetchrow.return_value = {"codigo": None, "candidatos": 0}
    response = await client.post("/api/v1/workflows/assign", json=payload, headers=TENANT_HEADERS)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "dependencia_desconocida"


@pytest.mark.asyncio
async def test_codigo_y_nombre_discordantes_422_y_el_texto_del_cliente_no_se_guarda(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, _ = client_with_conn
    payload = {**_ASSIGN_PAYLOAD, "to_dept_code": 4, "to_dept": "Otra Cosa"}
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, "Dirección Jurídica"]
    response = await client.post("/api/v1/workflows/assign", json=payload, headers=TENANT_HEADERS)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "dependencia_inconsistente"
    assert not any("INSERT INTO flow_steps" in c.args[0] for c in mock_conn.fetchrow.await_args_list)


# ---------------------------------------------------------------------------
# El ORIGEN lo deriva el servidor del paso activo
# ---------------------------------------------------------------------------

_REPO = "app.services.workflow_service.WorkflowRepository"


@pytest.mark.asyncio
async def test_transfer_deriva_el_origen_del_paso_activo(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """Sin `from_dept` en el cuerpo (ya no es obligatorio): el origen es el paso activo."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, "Secretaría General", 2, None]
    mock_conn.fetchrow.return_value = make_step_record(step_number=2, action="transfer")
    with patch(f"{_REPO}.paso_activo", new=AsyncMock(return_value=_activo())):
        response = await client.post(
            "/api/v1/workflows/transfer", json=_TRANSFER_PAYLOAD, headers=TENANT_HEADERS
        )
    assert response.status_code == 201
    insert = next(c for c in mock_conn.fetchrow.await_args_list if "INSERT INTO flow_steps" in c.args[0])
    assert "Dirección Jurídica" in insert.args and 4 in insert.args  # from_dept / from_dept_code


@pytest.mark.asyncio
async def test_origen_afirmado_que_ya_no_coincide_409(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    """Control optimista: el cliente vio el radicado en la dependencia 9 y ya está en la 4.
    Mutación: confiar en el `from_dept_code` del cliente (o ignorarlo) deja 201."""
    client, mock_conn, mock_redis = client_with_conn
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, "Secretaría General"]
    payload = {**_TRANSFER_PAYLOAD, "from_dept_code": 9}
    with patch(f"{_REPO}.paso_activo", new=AsyncMock(return_value=_activo(codigo=4))):
        response = await client.post("/api/v1/workflows/transfer", json=payload, headers=TENANT_HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "origen_desactualizado"
    assert not any("INSERT INTO flow_steps" in c.args[0] for c in mock_conn.fetchrow.await_args_list)
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_origen_afirmado_que_coincide_pasa(
    client_with_conn: tuple[AsyncClient, object, object],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, TRACKING_NUMBER, "Secretaría General", 2, None]
    mock_conn.fetchrow.return_value = make_step_record(step_number=2, action="transfer")
    payload = {**_TRANSFER_PAYLOAD, "from_dept_code": 4}
    with patch(f"{_REPO}.paso_activo", new=AsyncMock(return_value=_activo(codigo=4))):
        response = await client.post("/api/v1/workflows/transfer", json=payload, headers=TENANT_HEADERS)
    assert response.status_code == 201
