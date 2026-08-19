# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del archivo físico: ubicaciones, unidades, signatura, vínculo (E17)."""

import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.fisico_service as fisico_service_module
from app.schemas.fisico import PrestamoCreate
from app.services.fisico_service import FisicoService
from tests.conftest import TENANT_HEADERS, TENANT_SLUG, USER_ID

NOW = datetime.now(tz=UTC)


def _audit_log_calls(mock_conn) -> list[tuple]:
    """Extrae los `execute(...)` que insertan en `public.audit_log`.

    args: (sql, tenant, service, actor, canal, action, object_type,
    object_ref, payload, prev_hash, hash) -- mismo orden que `test_trd.py`.
    """
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


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
            "tipo": "caja",
            "codigo": "C0124",
            "signatura": "DEP01-C0124",
            "ubicacion_id": uuid.uuid4(),
            "folio_inicio": 1,
            "folio_fin": 200,
        }
    ]
    where = await client.get(f"/api/v1/expedientes/{exp_id}/unidades", headers=TENANT_HEADERS)
    assert where.status_code == 200
    assert where.json()[0]["signatura"] == "DEP01-C0124"
    # La signatura dice en qué entrepaño mirar; lo que se saca del estante es
    # «la caja C0124». Sin estos dos campos la pantalla pintaba guiones.
    assert where.json()[0]["tipo"] == "caja"
    assert where.json()[0]["codigo"] == "C0124"


@pytest.mark.asyncio
async def test_localizar_trae_la_identidad_de_la_unidad_desde_la_consulta(
    client_with_conn,
) -> None:
    """Sobre el SQL ENVIADO, no sobre la fila simulada.

    Las dos aserciones de arriba leen un diccionario escrito a mano: comprueban
    que el esquema ACEPTA `tipo` y `codigo`, no que la consulta los TRAIGA.
    Quitarlos del SELECT dejaba los tests en verde — verificado por mutación,
    y es la segunda vez que este mismo descuido aparece en esta revisión.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    consultas: list[str] = []

    async def fake_fetch(query, *params):
        consultas.append(query)
        return []

    mock_conn.fetch.side_effect = fake_fetch

    await client.get(f"/api/v1/expedientes/{uuid.uuid4()}/unidades", headers=TENANT_HEADERS)

    sql = next(q for q in consultas if "expediente_unidad" in q)
    assert "u.tipo" in sql
    assert "u.codigo" in sql


@pytest.mark.asyncio
async def test_expedientes_in_unit_row_level_filter(client_with_conn) -> None:
    """RF-SEG-08: `expedientes_in_unit` filtra POR FILA (`e.nivel_seguridad <=
    $2`, `$2` ligado al `max_clearance` real del llamante), no en
    post-proceso Python — mismo patrón que `test_fuid_inventory_row_level_filter`.
    Un `fetch` falso simula lo que haría Postgres: solo devuelve las filas
    cuyo nivel de origen sea <= `max_clearance`, así el test rompe si el
    código deja de pasar `max_clearance` a la query, o si alguien lo
    sustituye por un filtro Python aguas abajo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    unidad_id = uuid.uuid4()

    exp_publica = uuid.uuid4()
    exp_clasificada = uuid.uuid4()
    niveles = {exp_publica: 1, exp_clasificada: 3}

    async def fake_fetch(query, *params):
        assert "eu.unidad_id = $1" in query
        assert "e.nivel_seguridad <= $2" in query
        assert params[0] == unidad_id
        max_clearance = params[1]
        return [
            {"expediente_id": eid, "folio_inicio": 1, "folio_fin": 10}
            for eid in (exp_publica, exp_clasificada)
            if niveles[eid] <= max_clearance
        ]

    mock_conn.fetch.side_effect = fake_fetch

    # Caller de clearance PUBLICA(1): resolve_caller_clearance sin fila RBAC.
    mock_conn.fetchrow.return_value = None
    resp_low = await client.get(
        f"/api/v1/unidades/{unidad_id}/expedientes", headers=TENANT_HEADERS
    )
    assert resp_low.status_code == 200
    body_low = resp_low.json()
    assert len(body_low) == 1
    assert body_low[0]["expediente_id"] == str(exp_publica)

    # Misma unidad, caller root (CLASIFICADA=3): ambos expedientes visibles.
    mock_conn.fetchrow.return_value = {"is_root": True, "max_level": None}
    resp_high = await client.get(
        f"/api/v1/unidades/{unidad_id}/expedientes", headers=TENANT_HEADERS
    )
    assert resp_high.status_code == 200
    body_high = resp_high.json()
    assert {r["expediente_id"] for r in body_high} == {str(exp_publica), str(exp_clasificada)}


@pytest.mark.asyncio
async def test_expedientes_in_unit_sin_user_id_403(client_with_conn) -> None:
    """Fail-closed observado (no asumido): `check_permission` corta en
    `user_id is None -> return False` ANTES de tocar la BD (ver
    `app/core/clearance.py`), así que sin `X-User-Id` la petición nunca llega
    a `caller_clearance` — `require_permission` ya la rechaza con 403. El
    mock de `fetchval` deliberadamente devuelve `True` para demostrar que
    esto NO depende de la respuesta RBAC: el corte ocurre antes de
    consultarla (mismo patrón que `test_fuid_sin_user_id_403`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # irrelevante: el corte es por user_id=None
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    resp = await client.get(f"/api/v1/unidades/{uuid.uuid4()}/expedientes", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # Ni `caller_clearance` (resolve_caller_clearance) ni la query de fila
    # se ejecutaron: el 403 corta ANTES de llegar al servicio.
    mock_conn.fetchrow.assert_not_called()
    mock_conn.fetch.assert_not_called()


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
        # Un préstamo dice QUÉ falta del estante: las cuatro rutas hacen JOIN
        # con la unidad y traen su código y su signatura, no solo el UUID.
        "unidad_codigo": "C0124",
        "unidad_signatura": "DEP01-C0124",
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
        "unidad_codigo": "C0124",
        "soporte": "papel",
        "num_folios": 200,
        "folio_inicio": 1,
        "folio_fin": 200,
        # Columna 9 del formato (11.11): la calcula Postgres en la propia
        # consulta a partir de la pista de acceso.
        "frecuencia_consulta": "baja",
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


# ---------------------------------------------------------------------------
# Cadena de custodia física (Ley 594/2000 art. 26): asientos de auditoría
# de los 5 endpoints mutantes de `fisico.py`. Hallazgo Alto del auditor de
# conformidad -- `fisico_service.py` tenía 8 llamadas a `audit.append` sin
# UN SOLO test que las verificara; un refactor podía borrarlas sin que nadie
# se enterara. Sigue el patrón de `test_trd.py::test_create_trd_serie_writes_audit_entry`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_ubicacion_writes_audit_entry(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.fetchrow.return_value = _ubicacion_row(codigo="DEP01")
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

    audit_calls = _audit_log_calls(mock_conn)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.ubicacion_created"
    assert audit_calls[0][6] == "ubicacion"
    assert audit_calls[0][7] == "DEP01"
    payload = json.loads(audit_calls[0][8])
    assert payload["codigo"] == "DEP01"
    assert payload["nivel"] == "deposito"


@pytest.mark.asyncio
async def test_create_unidad_writes_audit_entry(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.fetchrow.return_value = _unidad_row(signatura=None, codigo="C0124")
    mock_conn.fetch.return_value = []  # sin ubicacion_id -> no ubicacion_path

    resp = await client.post(
        "/api/v1/unidades",
        json={"tipo": "caja", "codigo": "C0124", "soporte": "papel", "num_folios": 200},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201

    audit_calls = _audit_log_calls(mock_conn)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.unidad_created"
    assert audit_calls[0][6] == "unidad"
    # object_ref = signatura si existe, si no el codigo (aquí sin ubicacion -> codigo)
    assert audit_calls[0][7] == "C0124"
    payload = json.loads(audit_calls[0][8])
    assert payload["codigo"] == "C0124"
    assert payload["tipo"] == "caja"


@pytest.mark.asyncio
async def test_create_unidad_writes_audit_entry_con_signatura(client_with_conn) -> None:
    """object_ref usa la SIGNATURA (no el código pelado) cuando la unidad
    está ubicada -- es el identificador topográfico real de custodia."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate
    ub_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _ubicacion_row(id=ub_id, codigo="DEP01"),
        _unidad_row(ubicacion_id=ub_id, signatura="DEP01-C0124", codigo="C0124"),
    ]
    mock_conn.fetch.return_value = [{"codigo": "DEP01"}]

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

    audit_calls = _audit_log_calls(mock_conn)
    assert len(audit_calls) == 1
    assert audit_calls[0][7] == "DEP01-C0124"
    payload = json.loads(audit_calls[0][8])
    assert payload["signatura"] == "DEP01-C0124"


@pytest.mark.asyncio
async def test_link_expediente_writes_audit_entry(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate
    unidad_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = _unidad_row(id=unidad_id)  # get_unidad -> existe
    mock_conn.execute.return_value = None

    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/expedientes",
        json={"expediente_id": str(exp_id), "folio_inicio": 1, "folio_fin": 200},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204

    audit_calls = _audit_log_calls(mock_conn)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.unidad_expediente_linked"
    assert audit_calls[0][6] == "unidad"
    assert audit_calls[0][7] == str(unidad_id)
    payload = json.loads(audit_calls[0][8])
    assert payload["expediente_id"] == str(exp_id)
    assert payload["folio_inicio"] == 1
    assert payload["folio_fin"] == 200


@pytest.mark.asyncio
async def test_create_prestamo_writes_audit_entry(client_with_conn) -> None:
    """Cadena de custodia -- alta del préstamo: A QUIÉN se prestó y la fecha
    PREVISTA de devolución quedan en el payload del asiento (art. 26 Ley
    594/2000). `prestado_por` (quién lo entrega) también viaja, aunque el
    auditor solo exige los dos primeros."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate
    unidad_id = uuid.uuid4()
    prestamo_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),  # get_unidad
        None,  # active_loan -> ninguno
        _prestamo_row(id=prestamo_id, unidad_id=unidad_id),  # insert_prestamo
    ]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos",
        json={"prestado_a": "Jurídica", "fecha_devolucion_esperada": "2099-01-01"},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201

    audit_calls = _audit_log_calls(mock_conn)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.prestamo_created"
    assert audit_calls[0][6] == "prestamo"
    assert audit_calls[0][7] == str(prestamo_id)
    payload = json.loads(audit_calls[0][8])
    assert payload["unidad_id"] == str(unidad_id)
    assert payload["prestado_a"] == "Jurídica"
    assert payload["fecha_devolucion_esperada"] == "2099-01-01"


@pytest.mark.asyncio
async def test_devolver_prestamo_writes_audit_entry(client_with_conn) -> None:
    """Cadena de custodia -- cierre del préstamo: el payload debe permitir
    reconstruir A QUIÉN se había prestado, la fecha PREVISTA y la fecha
    REAL de devolución -- los tres datos que exige el auditor. Los mocks
    devuelven filas DISTINTAS para el estado previo y el resultado de la
    devolución (si fueran la misma fila, `fecha_devolucion_real` saldría
    `None` en ambos lados y el test no distinguiría un bug de un acierto)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate
    pid = uuid.uuid4()
    unidad_id = uuid.uuid4()
    devuelto_ts = datetime(2099, 3, 1, 12, 0, tzinfo=UTC)
    mock_conn.fetchrow.side_effect = [
        _prestamo_row(id=pid, unidad_id=unidad_id, prestado_a="Jurídica"),  # get_prestamo (previo)
        _prestamo_row(  # devolver_prestamo (fila DISTINTA: ya devuelta)
            id=pid,
            unidad_id=unidad_id,
            prestado_a="Jurídica",
            estado="devuelto",
            fecha_devolucion_real=devuelto_ts,
        ),
    ]
    resp = await client.post(f"/api/v1/prestamos/{pid}/devolver", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    audit_calls = _audit_log_calls(mock_conn)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.prestamo_devuelto"
    assert audit_calls[0][6] == "prestamo"
    assert audit_calls[0][7] == str(pid)
    payload = json.loads(audit_calls[0][8])
    assert payload["unidad_id"] == str(unidad_id)
    # Los tres datos de custodia que exige el auditor, en el MISMO asiento:
    assert payload["prestado_a"] == "Jurídica"
    assert payload["fecha_devolucion_esperada"] == date(2099, 1, 1).isoformat()
    assert payload["fecha_devolucion_real"] == devuelto_ts.isoformat()


# ---------------------------------------------------------------------------
# Fail-closed: si `audit.append` revienta, la mutación NO se confirma como
# éxito. Se invoca el servicio directamente (mismo patrón que
# `test_expediente_metadata_audit.py::...late_failure_propagates_not_a_fake_success`),
# porque `client_with_conn` mockea `conn.transaction()` como un context
# manager vacío: no hay rollback real de Postgres que observar aquí (eso es
# tier integración, ver `tests/integration/`), pero SÍ podemos comprobar que
# la excepción se propaga hasta el llamante en vez de ser tragada -- que es
# la diferencia entre "el endpoint responde 500" y "el endpoint responde 201
# con un préstamo/devolución sin rastro forense".
# ---------------------------------------------------------------------------


async def _boom(*args: object, **kwargs: object) -> str:
    raise RuntimeError("audit failure (simulated)")


@pytest.mark.asyncio
async def test_create_prestamo_audit_failure_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = None
    mock_conn.fetch.return_value = []
    mock_conn.transaction = MagicMock(return_value=AsyncMock())

    unidad_id = uuid.uuid4()
    prestamo_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),  # get_unidad
        None,  # active_loan
        _prestamo_row(id=prestamo_id, unidad_id=unidad_id),  # insert_prestamo (nunca debe "ganar")
    ]

    monkeypatch.setattr(fisico_service_module.audit, "append", _boom)

    service = FisicoService(mock_conn, TENANT_SLUG)
    payload = PrestamoCreate(prestado_a="Jurídica", fecha_devolucion_esperada=date(2099, 1, 1))

    with pytest.raises(RuntimeError, match="audit failure"):
        await service.create_prestamo(unidad_id, payload, "ana", actor=USER_ID)

    # El INSERT del préstamo (y su movimiento RETIRADO) ya se había pedido
    # dentro de la MISMA transacción cuando `audit.append` reventó -- el
    # fallo es TARDÍO, no evita que se intente la escritura, pero SÍ evita
    # que el llamante reciba una `PrestamoResponse` de éxito sin asiento.
    assert mock_conn.fetchrow.call_count == 3


@pytest.mark.asyncio
async def test_devolver_prestamo_audit_failure_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = None
    mock_conn.fetch.return_value = []
    mock_conn.transaction = MagicMock(return_value=AsyncMock())

    pid = uuid.uuid4()
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _prestamo_row(id=pid, unidad_id=unidad_id),  # get_prestamo
        _prestamo_row(  # devolver_prestamo
            id=pid, unidad_id=unidad_id, estado="devuelto", fecha_devolucion_real=NOW
        ),
    ]

    monkeypatch.setattr(fisico_service_module.audit, "append", _boom)

    service = FisicoService(mock_conn, TENANT_SLUG)

    with pytest.raises(RuntimeError, match="audit failure"):
        await service.devolver_prestamo(pid, "ana", actor=USER_ID)

    assert mock_conn.fetchrow.call_count == 2


# ── 11.11: el FUID salía sin cabecera ────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_fuid_lleva_cabecera_con_la_entidad_productora(client_with_conn) -> None:
    """Una lista de filas sin decir de qué entidad y de qué oficina es no es un
    FUID: el receptor de una transferencia no tiene cómo saber qué recibe
    (Ac. AGN 042/2002, Anexo)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = "Instituto Colombiano de Crédito Educativo"
    mock_conn.fetch.return_value = [_fuid_inv_row()]

    resp = await client.get(
        "/api/v1/fuid",
        params={
            "unidad_administrativa": "Subdirección Administrativa",
            "oficina_productora": "Grupo de Contratación",
            "objeto": "Transferencia primaria 2026",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    cabecera = resp.json()["cabecera"]
    assert cabecera["entidad_productora"] == "Instituto Colombiano de Crédito Educativo"
    assert cabecera["unidad_administrativa"] == "Subdirección Administrativa"
    assert cabecera["oficina_productora"] == "Grupo de Contratación"
    assert cabecera["objeto"] == "Transferencia primaria 2026"


@pytest.mark.asyncio
async def test_la_entidad_productora_no_la_elige_el_cliente(client_with_conn) -> None:
    """Encabeza un documento oficial: un campo libre convertiría el inventario
    de una entidad en el de cualquier otra con solo cambiar un parámetro."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = "Entidad Real"
    mock_conn.fetch.return_value = [_fuid_inv_row()]

    resp = await client.get(
        "/api/v1/fuid",
        params={"entidad_productora": "Entidad Suplantada"},
        headers=TENANT_HEADERS,
    )

    assert resp.json()["cabecera"]["entidad_productora"] == "Entidad Real"


@pytest.mark.asyncio
async def test_el_xml_del_fuid_lleva_cabecera_y_frecuencia(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = "Entidad Real"
    mock_conn.fetch.return_value = [_fuid_inv_row(frecuencia_consulta="alta")]

    resp = await client.get(
        "/api/v1/fuid.xml", params={"objeto": "Entrega de cargo"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    assert "<cabecera" in resp.text or "cabecera" in resp.text
    assert "Entidad Real" in resp.text
    assert "Entrega de cargo" in resp.text
    assert "alta" in resp.text


@pytest.mark.asyncio
async def test_la_frecuencia_de_consulta_se_deriva_de_la_pista_de_acceso(
    client_with_conn,
) -> None:
    """Columna 9 del formato. En papel es una estimación de memoria; aquí sale de
    `audit_log`, que el servicio ya escribe en cada consulta de expediente.

    El filtro por `tenant_slug` es lo que se comprueba con más cuidado:
    `audit_log` está en `public` y es la ÚNICA tabla compartida entre entidades,
    así que sin él dos entidades con códigos de expediente coincidentes —y
    `code` es un consecutivo— se contarían las consultas la una a la otra.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = "Entidad Real"
    consultas: list[str] = []

    async def fake_fetch(query, *params):
        consultas.append(query)
        return [_fuid_inv_row()]

    mock_conn.fetch.side_effect = fake_fetch

    await client.get("/api/v1/fuid", headers=TENANT_HEADERS)

    inventario = next(q for q in consultas if "expediente_unidad" in q)
    assert "public.audit_log" in inventario
    assert "al.tenant_slug" in inventario
    assert "12 months" in inventario


# ---------------------------------------------------------------------------
# El árbol de ubicaciones tiene que ser un árbol (revisión por navegador, B8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solo_raiz_pide_el_primer_nivel(client_with_conn) -> None:
    """`solo_raiz=true` filtra `parent_id IS NULL` en la CONSULTA.

    La pantalla de archivo físico pinta el primer nivel del árbol y luego
    despliega hijos. Pedía `?activo=true` a secas creyendo —lo decía su
    comentario— que sin `parent_id` el backend devuelve las raíces. No: sin
    `parent_id` no filtra por padre, que es lo natural en un listado. El
    resultado era que cada depósito, estante y gaveta aparecía también como
    raíz y el árbol dejaba de ser un árbol.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    consultas: list[str] = []

    async def fake_fetch(query, *params):
        consultas.append(query)
        return []

    mock_conn.fetch.side_effect = fake_fetch

    resp = await client.get("/api/v1/ubicaciones?solo_raiz=true", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    # La aserción mira la cláusula WHERE DEL LISTADO, aislada: el CTE que
    # resuelve la ruta lleva su propio `a.parent_id IS NULL` y buscar la cadena
    # en el SQL entero daba verde aunque `solo_raiz` no hiciera nada. Lo cazó
    # el gemelo de abajo, no yo.
    sql = next(q for q in consultas if "FROM ubicacion" in q)
    assert "parent_id IS NULL" in _where_del_listado(sql)


def _where_del_listado(sql: str) -> str:
    """La cola de la consulta desde el `FROM ubicacion` de más afuera."""
    return sql.rsplit("FROM ubicacion", 1)[-1]


@pytest.mark.asyncio
async def test_sin_filtros_sigue_devolviendo_todas(client_with_conn) -> None:
    """Gemelo: el atajo perezoso era hacer que «sin parent_id» significara raíces.

    Habría arreglado la pantalla del árbol y roto en silencio a todo el que
    lista ubicaciones para elegir una — que es justo lo que hacen los
    desplegables que sustituyen a los campos de UUID.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    consultas: list[str] = []

    async def fake_fetch(query, *params):
        consultas.append(query)
        return []

    mock_conn.fetch.side_effect = fake_fetch

    await client.get("/api/v1/ubicaciones", headers=TENANT_HEADERS)

    sql = next(q for q in consultas if "FROM ubicacion" in q)
    assert "WHERE" not in _where_del_listado(sql)


@pytest.mark.asyncio
async def test_solo_raiz_y_parent_id_no_se_combinan(client_with_conn) -> None:
    """O el primer nivel, o los hijos de una ubicación: pedir las dos cosas es
    una petición sin significado, y responder «las raíces» en silencio sería
    ignorar la mitad de lo que se pidió."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.get(
        f"/api/v1/ubicaciones?solo_raiz=true&parent_id={uuid.uuid4()}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "filtros_incompatibles"


@pytest.mark.asyncio
async def test_el_listado_lleva_la_ruta_de_cada_ubicacion(client_with_conn) -> None:
    """Un código suelto (`EST-003`) no dice dónde está. La ruta completa se
    resuelve para todas las filas con un CTE, no una consulta por fila."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    consultas: list[str] = []

    async def fake_fetch(query, *params):
        consultas.append(query)
        return [_ubicacion_row(ruta="SED-001 / DEP-001")]

    mock_conn.fetch.side_effect = fake_fetch

    resp = await client.get("/api/v1/ubicaciones", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["ruta"] == "SED-001 / DEP-001"

    sql = next(q for q in consultas if "FROM ubicacion" in q)
    assert "RECURSIVE" in sql
    # Una consulta por fila sería N+1 sobre el inventario entero.
    assert len([q for q in consultas if "FROM ubicacion" in q]) == 1


@pytest.mark.asyncio
async def test_el_prestamo_dice_que_unidad_falta_del_estante(client_with_conn) -> None:
    """El listado de préstamos traía solo `unidad_id`.

    Nadie busca un UUID en un depósito. Se comprueba sobre el SQL ENVIADO, no
    sobre el esquema: que `PrestamoResponse` acepte el campo no prueba que la
    consulta lo traiga — esa confusión ya dejó pasar un test en esta revisión.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    consultas: list[str] = []

    async def fake_fetch(query, *params):
        consultas.append(query)
        return [_prestamo_row()]

    mock_conn.fetch.side_effect = fake_fetch

    resp = await client.get("/api/v1/prestamos?estado=prestado", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["unidad_codigo"] == "C0124"

    sql = next(q for q in consultas if "FROM prestamo" in q)
    assert "JOIN unidad_conservacion" in sql
    # El JOIN introduce dos columnas `estado`/`unidad_id` homónimas: sin
    # prefijar, Postgres aborta con «column reference is ambiguous».
    assert "p.estado" in sql
