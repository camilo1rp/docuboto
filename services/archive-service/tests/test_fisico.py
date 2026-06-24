# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del archivo físico: ubicaciones, unidades, signatura, vínculo (E17)."""

import uuid
from datetime import UTC, date, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


def _ubicacion_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "parent_id": None,
        "nivel": "deposito",
        "codigo": "DEP01",
        "nombre": "Depósito 1",
        "tipo_archivo": "central",
        "capacidad_ml": None,
        "activo": True,
        "created_at": NOW,
    }
    base.update(kw)
    return base


def _unidad_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "parent_id": None,
        "ubicacion_id": None,
        "tipo": "caja",
        "codigo": "C0124",
        "signatura": None,
        "soporte": "papel",
        "num_folios": 200,
        "activo": True,
        "created_at": NOW,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_ubicacion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _ubicacion_row()
    mock_conn.fetch.return_value = [{"codigo": "DEP01"}]  # ubicacion_path

    resp = await client.post(
        "/api/v1/ubicaciones",
        json={
            "nivel": "deposito",
            "codigo": "DEP01",
            "nombre": "Depósito 1",
            "tipo_archivo": "central",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["codigo"] == "DEP01"
    assert body["ruta"] == "DEP01"


@pytest.mark.asyncio
async def test_create_unidad_computes_signatura(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    ub_id = uuid.uuid4()
    # get_ubicacion (existe), insert_unidad (RETURNING con signatura)
    mock_conn.fetchrow.side_effect = [
        _ubicacion_row(id=ub_id, codigo="DEP01"),
        _unidad_row(ubicacion_id=ub_id, signatura="DEP01-E05-C0124"),
    ]
    # ubicacion_path → DEP01, E05
    mock_conn.fetch.return_value = [{"codigo": "DEP01"}, {"codigo": "E05"}]

    resp = await client.post(
        "/api/v1/unidades",
        json={
            "tipo": "caja",
            "codigo": "C0124",
            "ubicacion_id": str(ub_id),
            "soporte": "papel",
            "num_folios": 200,
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["signatura"] == "DEP01-E05-C0124"


@pytest.mark.asyncio
async def test_link_and_locate_expediente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    unidad_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = _unidad_row(id=unidad_id)  # get_unidad → existe
    mock_conn.execute.return_value = None

    link = await client.post(
        f"/api/v1/unidades/{unidad_id}/expedientes",
        json={"expediente_id": str(exp_id), "folio_inicio": 1, "folio_fin": 200},
        headers=TENANT_HEADERS,
    )
    assert link.status_code == 204

    # "dónde está el expediente"
    mock_conn.fetch.return_value = [
        {
            "unidad_id": unidad_id,
            "signatura": "DEP01-C0124",
            "ubicacion_id": uuid.uuid4(),
            "folio_inicio": 1,
            "folio_fin": 200,
        }
    ]
    where = await client.get(f"/api/v1/expedientes/{exp_id}/unidades", headers=TENANT_HEADERS)
    assert where.status_code == 200
    assert where.json()[0]["signatura"] == "DEP01-C0124"


@pytest.mark.asyncio
async def test_get_unidad_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/unidades/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


# -- préstamos y movimientos (RF-ARF-07/08) ---------------------------


def _prestamo_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "unidad_id": uuid.uuid4(),
        "prestado_a": "Jurídica",
        "prestado_por": "ana",
        "fecha_prestamo": NOW,
        "fecha_devolucion_esperada": date(2099, 1, 1),
        "fecha_devolucion_real": None,
        "estado": "prestado",
        "created_at": NOW,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_prestamo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    unidad_id = uuid.uuid4()
    # get_unidad (existe), active_loan (None), insert_prestamo (row)
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,
        _prestamo_row(unidad_id=unidad_id),
    ]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos",
        json={"prestado_a": "Jurídica", "fecha_devolucion_esperada": "2099-01-01"},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["estado"] == "prestado"
    assert body["vencido"] is False


@pytest.mark.asyncio
async def test_create_prestamo_already_loaned(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    unidad_id = uuid.uuid4()
    # get_unidad (existe), active_loan (existe → 409)
    mock_conn.fetchrow.side_effect = [_unidad_row(id=unidad_id), {"id": uuid.uuid4()}]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos",
        json={"prestado_a": "X", "fecha_devolucion_esperada": "2099-01-01"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_devolver_prestamo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    # get_prestamo (existe, prestado), devolver_prestamo (row devuelto)
    mock_conn.fetchrow.side_effect = [
        _prestamo_row(id=pid),
        _prestamo_row(id=pid, estado="devuelto", fecha_devolucion_real=NOW),
    ]
    resp = await client.post(f"/api/v1/prestamos/{pid}/devolver", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["estado"] == "devuelto"


def _fuid_inv_row(**kw) -> dict:
    base = {
        "expediente_id": uuid.uuid4(),
        "expediente_code": "EXP-2024-0001",
        "asunto": "Contrato de obra",
        "fecha_inicial": NOW,
        "fecha_final": None,
        "serie_code": "100",
        "serie_name": "Contratos",
        "signatura": "DEP01-C0124",
        "unidad_tipo": "caja",
        "soporte": "papel",
        "num_folios": 200,
        "folio_inicio": 1,
        "folio_fin": 200,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_fuid_inventory_json(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_fuid_inv_row(), _fuid_inv_row(expediente_code="EXP-2024-0002")]

    resp = await client.get("/api/v1/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["rows"][0]["orden"] == 1
    assert body["rows"][0]["signatura"] == "DEP01-C0124"


@pytest.mark.asyncio
async def test_fuid_export_xml(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_fuid_inv_row()]

    resp = await client.get("/api/v1/fuid.xml", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "urn:orpycamcp:fuid:v1" in resp.text
    assert "EXP-2024-0001" in resp.text


@pytest.mark.asyncio
async def test_list_movimientos(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    unidad_id = uuid.uuid4()
    mock_conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "unidad_id": unidad_id,
            "accion": "RETIRADO",
            "ubicacion_before": None,
            "ubicacion_after": None,
            "performed_by": "ana",
            "ts": NOW,
        },
    ]
    resp = await client.get(f"/api/v1/unidades/{unidad_id}/movimientos", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["accion"] == "RETIRADO"
