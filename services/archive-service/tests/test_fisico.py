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
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
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
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
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
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
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
    mock_conn.fetchval.return_value = True  # D-08: permission ok; service raises 404
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
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
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
    mock_conn.fetchval.return_value = True  # D-08: permission ok; service raises 409
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
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
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
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
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
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetch.return_value = [_fuid_inv_row()]

    resp = await client.get("/api/v1/fuid.xml", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "urn:orpycamcp:fuid:v1" in resp.text
    assert "EXP-2024-0001" in resp.text


@pytest.mark.asyncio
async def test_fuid_inventory_row_level_filter(client_with_conn) -> None:
    """RF-SEG-08 V2: `fuid_inventory` filtra POR FILA (`e.nivel_seguridad <=
    $1`, `$1` ligado al `max_clearance` real del llamante), no en
    post-proceso Python. Un `fetch` falso simula lo que haría Postgres:
    solo devuelve las filas cuyo nivel de origen sea <= `max_clearance` —
    así el test rompe si el código deja de pasar `max_clearance` a la query,
    o si alguien lo sustituye por un filtro Python aguas abajo (indistinguible
    para un mock que no ejecuta SQL de verdad)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted

    row_publica = _fuid_inv_row(expediente_code="EXP-PUB")
    row_clasificada = _fuid_inv_row(expediente_code="EXP-CLA")
    niveles = {"EXP-PUB": 1, "EXP-CLA": 3}

    async def fake_fetch(query, *params):
        assert "e.nivel_seguridad <= $1" in query
        max_clearance = params[0]
        return [
            r for r in (row_publica, row_clasificada) if niveles[r["expediente_code"]] <= max_clearance
        ]

    mock_conn.fetch.side_effect = fake_fetch

    # Caller de clearance PUBLICA(1): resolve_caller_clearance sin fila RBAC.
    mock_conn.fetchrow.return_value = None
    resp_low = await client.get("/api/v1/fuid", headers=TENANT_HEADERS)
    assert resp_low.status_code == 200
    body_low = resp_low.json()
    assert body_low["total"] == 1
    assert body_low["rows"][0]["expediente_code"] == "EXP-PUB"

    # Mismo inventario, caller root (CLASIFICADA=3): ambos visibles.
    mock_conn.fetchrow.return_value = {"is_root": True, "max_level": None}
    resp_high = await client.get("/api/v1/fuid", headers=TENANT_HEADERS)
    assert resp_high.status_code == 200
    body_high = resp_high.json()
    assert body_high["total"] == 2
    assert {r["expediente_code"] for r in body_high["rows"]} == {"EXP-PUB", "EXP-CLA"}


@pytest.mark.asyncio
async def test_fuid_xml_row_level_filter(client_with_conn) -> None:
    """Mismo filtro por-fila que `test_fuid_inventory_row_level_filter`, para
    la puerta `/fuid.xml` (`FisicoService.generate_fuid_xml` delega en el
    mismo `generate_fuid`): un expediente CLASIFICADA no aparece en el XML
    para un llamante sin clearance suficiente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted

    row_publica = _fuid_inv_row(expediente_code="EXP-PUB")
    row_clasificada = _fuid_inv_row(expediente_code="EXP-CLA")
    niveles = {"EXP-PUB": 1, "EXP-CLA": 3}

    async def fake_fetch(query, *params):
        assert "e.nivel_seguridad <= $1" in query
        max_clearance = params[0]
        return [
            r for r in (row_publica, row_clasificada) if niveles[r["expediente_code"]] <= max_clearance
        ]

    mock_conn.fetch.side_effect = fake_fetch
    mock_conn.fetchrow.return_value = None  # caller_clearance -> PUBLICA(1)

    resp = await client.get("/api/v1/fuid.xml", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert "EXP-PUB" in resp.text
    assert "EXP-CLA" not in resp.text


@pytest.mark.asyncio
async def test_fuid_sin_user_id_403(client_with_conn) -> None:
    """Fail-closed observado (no asumido): `check_permission` corta en
    `user_id is None -> return False` ANTES de tocar la BD (ver
    `app/core/clearance.py`), así que sin `X-User-Id` la petición nunca llega
    a `caller_clearance` — `require_permission` ya la rechaza con 403. El
    mock de `fetchval` deliberadamente devuelve `True` para demostrar que
    esto NO depende de la respuesta RBAC: el corte ocurre antes de consultarla."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # irrelevante: el corte es por user_id=None
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    resp = await client.get("/api/v1/fuid", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_movimientos(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
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


# ---------------------------------------------------------------------------
# D-08: comprobaciones de autorización (USUA_PERM_EXPEDIENTE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_ubicacion_sin_permiso_403(client_with_conn) -> None:
    """check_permission usa fetchval; None → usuario no encontrado → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → sin permiso
    resp = await client.post(
        "/api/v1/ubicaciones",
        json={"nivel": "deposito", "codigo": "DEP01", "nombre": "Dep 1", "tipo_archivo": "central"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_unidad_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        "/api/v1/unidades",
        json={"tipo": "caja", "codigo": "C0001", "soporte": "papel", "num_folios": 100},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_prestamo_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/unidades/{uuid.uuid4()}/prestamos",
        json={"prestado_a": "Jurídica", "fecha_devolucion_esperada": "2099-01-01"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_ubicaciones_sin_permiso_403(client_with_conn) -> None:
    """GET también requiere USUA_PERM_EXPEDIENTE (inventario de archivo)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get("/api/v1/ubicaciones", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_fuid_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get("/api/v1/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_devolver_prestamo_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/prestamos/{uuid.uuid4()}/devolver",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_units_of_expediente_sin_permiso_403(client_with_conn) -> None:
    """Faceta 'Ubicación física' del workspace de expediente (T-10, frontend SSR)
    requiere USUA_PERM_EXPEDIENTE. El frontend usa Promise.allSettled, por lo que
    el fallo de esta faceta no bloquea el resto de la vista."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        f"/api/v1/expedientes/{uuid.uuid4()}/unidades",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_ubicacion_con_permiso_201(client_with_conn) -> None:
    """fetchval truthy → permission ok; fetchrow → INSERT ubicación."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = _ubicacion_row()
    mock_conn.fetch.return_value = [{"codigo": "DEP01"}]

    resp = await client.post(
        "/api/v1/ubicaciones",
        json={"nivel": "deposito", "codigo": "DEP01", "nombre": "Dep 1", "tipo_archivo": "central"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["codigo"] == "DEP01"
