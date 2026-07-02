# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for archive-service endpoints.

DB is fully mocked — no real infrastructure required.

Key mock call sequences:

CREATE EXPEDIENTE:
  execute  — SET search_path (next_expediente_code)
  execute  — upsert expediente_sequences
  fetchval — SELECT last_seq
  execute  — SET search_path (create_expediente)
  fetchrow — INSERT expedientes RETURNING

LIST EXPEDIENTES:
  execute  — SET search_path (fetchval)
  fetchval — COUNT
  execute  — SET search_path (fetch)
  fetch    — SELECT page

GET EXPEDIENTE BY ID:
  execute  — SET search_path
  fetchrow — SELECT expediente
  fetch    — SELECT radicados

LINK RADICADO (link_radicado calls get+exists+insert):
  execute  — SET search_path (get)
  fetchrow — SELECT expediente
  fetch    — SELECT radicados (empty)
  execute  — SET search_path (exists)
  fetchval — COUNT(*) for duplicate check  → 0
  execute  — SET search_path (link)
  fetchrow — INSERT expediente_radicados RETURNING

UNLINK RADICADO:
  execute  — SET search_path
  execute  — DELETE FROM expediente_radicados  (returns "DELETE 1")

CREATE TRD SERIE:
  execute  — SET search_path
  fetchrow — INSERT trd_series RETURNING

LIST TRD SERIES:
  execute  — SET search_path (fetchval)
  fetchval — COUNT
  execute  — SET search_path (fetch)
  fetch    — SELECT page
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import (
    EXPEDIENTE_ID,
    RADICADO_ID,
    SERIE_ID,
    TENANT_HEADERS,
    TRACKING_NUMBER,
    USER_ID,
    make_expediente_record,
    make_radicado_link_record,
    make_trd_serie_record,
)

_CREATE_EXP_PAYLOAD = {
    "name": "Contrato 2024-001",
    "description": "Contrato de prestación de servicios",
}

_CREATE_TRD_PAYLOAD = {
    "code": "100",
    "name": "Contratos",
    "retention_years": 5,
    "total_retention": 10,
    "disposition": "conserve",
}


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expediente(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, code="EXP-2024-0001")

    # fetchval returns the sequence number (1), fetchrow returns the new expediente.
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = exp_record

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "EXP-2024-0001"
    assert body["name"] == "Contrato 2024-001"
    assert body["status"] == "open"
    assert body["radicados"] == []


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes — missing X-Tenant-Slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expediente_missing_tenant(client: AsyncClient) -> None:
    """Sin X-Tenant-Slug, el gate RBAC (`require_permission`, dependency del
    decorador) corta la petición con 403 antes de que `_require_tenant` (Depends
    del handler) pueda emitir su propio 400 — `require_permission` exige el
    tenant para poder resolver el permiso y responde fail-closed (D-08)."""
    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers={"X-User-Id": str(USER_ID)},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# GET /api/v1/expedientes — list with X-Total-Count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_expedientes(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    records = [
        make_expediente_record(exp_id=uuid.uuid4(), code="EXP-2024-0001"),
        make_expediente_record(exp_id=uuid.uuid4(), code="EXP-2024-0002"),
    ]
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = records

    response = await client.get(
        "/api/v1/expedientes",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "2"
    body = response.json()
    assert len(body) == 2
    assert body[0]["code"] == "EXP-2024-0001"
    assert body[1]["code"] == "EXP-2024-0002"


# ---------------------------------------------------------------------------
# GET /api/v1/expedientes/{expediente_id} — includes radicados
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_expediente_by_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    rad_record = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = exp_record
    mock_conn.fetch.return_value = [rad_record]

    response = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(EXPEDIENTE_ID)
    assert len(body["radicados"]) == 1
    assert body["radicados"][0]["radicado_id"] == str(RADICADO_ID)
    assert body["radicados"][0]["tracking_number"] == TRACKING_NUMBER


@pytest.mark.asyncio
async def test_get_expediente_by_id_is_clearance_scoped(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """RF-SEG-08: el GET de detalle de expediente acota por clasificación — la
    consulta filtra `nivel_seguridad <= clearance`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/expedientes/{EXPEDIENTE_ID}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    # La consulta de detalle incluye el predicado de clearance.
    assert "nivel_seguridad <=" in mock_conn.fetchrow.call_args.args[0]


@pytest.mark.asyncio
async def test_list_expediente_eventos_is_clearance_scoped(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """E02 §9 / RF-SEG-08: la hoja de ruta del expediente se acota por la visibilidad del
    objeto para el clearance del llamante (Canal B), NO por permiso de admin."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = None  # usuario sin clearance → PUBLICA (1)
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/eventos", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    eventos_sql = mock_conn.fetch.call_args[0][0]
    assert "expediente_eventos" in eventos_sql
    assert "nivel_seguridad" in eventos_sql and "<=" in eventos_sql
    assert mock_conn.fetch.call_args[0][2] == 1  # clearance PUBLICA como parámetro


# ---------------------------------------------------------------------------
# GET /api/v1/expedientes/{expediente_id} — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_expediente_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/expedientes/{uuid.uuid4()}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"


# ---------------------------------------------------------------------------
# PATCH /api/v1/expedientes/{expediente_id} — close (open→closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_expediente_status(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    open_record = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    closed_record = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed")

    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # First fetchrow → get_expediente_by_id (current state check)
    # Second fetchrow → update_expediente RETURNING
    mock_conn.fetchrow.side_effect = [open_record, closed_record]
    # fetch → radicados list (empty for get_expediente_by_id call)
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"status": "closed"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "closed"


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes/{expediente_id}/radicados — link radicado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_radicado(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link_record = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)

    # fetchrow: get_expediente, link INSERT, [regenerate: get_expediente,
    # get_current(None), insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, link_record, exp_record, None, exp_record]
    # fetch → radicados (empty during get_expediente check)
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate (USUA_PERM_EXPEDIENTE granted), [1]=COUNT(*) duplicate check → 0,
    # [2]=audit.append prev_hash lookup (regenerate_index → índice E15, ADR-010)
    mock_conn.fetchval.side_effect = [True, 0, None]

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["radicado_id"] == str(RADICADO_ID)
    assert body["tracking_number"] == TRACKING_NUMBER


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes/{expediente_id}/radicados — duplicate → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_radicado_duplicate(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)

    mock_conn.fetchrow.return_value = exp_record
    mock_conn.fetch.return_value = []
    # fetchval → 1 means the link already exists
    mock_conn.fetchval.return_value = 1

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "radicado_already_linked"


# ---------------------------------------------------------------------------
# DELETE /api/v1/expedientes/{expediente_id}/radicados/{radicado_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_radicado(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # execute returns "DELETE 1" to indicate one row was removed
    mock_conn.execute.return_value = "DELETE 1"
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    # fetchrow: get_expediente (guard), [regenerate: get_expediente,
    # get_current(None), insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, exp_record, None, exp_record]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# POST /api/v1/trd — create TRD serie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_trd_serie(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    serie_record = make_trd_serie_record(serie_id=SERIE_ID)
    mock_conn.fetchrow.return_value = serie_record

    response = await client.post(
        "/api/v1/trd",
        json=_CREATE_TRD_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(SERIE_ID)
    assert body["code"] == "100"
    assert body["name"] == "Contratos"
    assert body["disposition"] == "conserve"
    assert body["is_active"] is True


# ---------------------------------------------------------------------------
# GET /api/v1/trd — list series with X-Total-Count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trd_series(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    records = [
        make_trd_serie_record(serie_id=uuid.uuid4(), code="100"),
        make_trd_serie_record(serie_id=uuid.uuid4(), code="200"),
    ]
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = records

    response = await client.get(
        "/api/v1/trd",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "2"
    body = response.json()
    assert len(body) == 2
    assert body[0]["code"] == "100"
    assert body[1]["code"] == "200"


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1

    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


@pytest.mark.asyncio
async def test_link_radicados_batch(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Inclusión masiva: vincula los nuevos, omite los ya vinculados, regenera índice 1 vez."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    # get_expediente_by_id (fetchrow exp) ; luego por cada radicado: link INSERT (fetchrow);
    # al final regenerate: get_expediente_by_id (fetchrow exp), get_current (None), insert índice
    mock_conn.fetchrow.side_effect = [
        exp,  # _expediente_or_404
        make_radicado_link_record(radicado_id=r1),  # link r1
        make_radicado_link_record(radicado_id=r2),  # link r2
        exp,
        None,
        exp,  # regenerate (get_exp, get_current, insert)
    ]
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate, [1]/[2]=radicado_link_exists por r1/r2 → 0 (ninguno vinculado
    # aún), [3]=audit.append prev_hash lookup (regenerate_index, 1 sola vez tras el batch)
    mock_conn.fetchval.side_effect = [True, 0, 0, None]

    resp = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/batch",
        json={
            "radicados": [
                {"radicado_id": str(r1), "tracking_number": "2024-A-001"},
                {"radicado_id": str(r2), "tracking_number": "2024-A-002"},
            ]
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vinculados"] == 2 and body["omitidos"] == 0 and body["total"] == 2


# ---------------------------------------------------------------------------
# RBAC gate (D-08): USUA_PERM_EXPEDIENTE en los 12 endpoints de expedientes.py
# (Alineación completa con indice.py/fisico.py/transferencias.py — cierre del
# gap: antes, cualquier usuario autenticado del tenant podía operar sobre
# expedientes sin el permiso, mutaciones y lecturas por igual.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expediente_forbidden_without_permission(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mutación representativa: sin USUA_PERM_EXPEDIENTE, `require_permission`
    corta con 403 antes de tocar la lógica de negocio (no se genera secuencia)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # D-08: permiso denegado

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_expediente_forbidden_without_permission(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Lectura representativa: sin USUA_PERM_EXPEDIENTE, GET también es 403 —
    D-08 es alineación completa, las lecturas no quedan exentas del gate."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # D-08: permiso denegado

    response = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_expediente_forbidden_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """`require_permission` es fail-closed sobre la IDENTIDAD: sin X-User-Id no
    hay usuario que resolver → 403, sin necesidad de que la BD deniegue nada
    (check_permission ni siquiera consulta con user_id=None)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_expediente_forbidden_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Misma conducta fail-closed para una lectura: sin X-User-Id, 403 antes de
    consultar el expediente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    response = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()
