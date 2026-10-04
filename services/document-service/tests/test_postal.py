# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de envíos postales (E20)."""

import uuid
from datetime import UTC, datetime

import pytest

from app.services.postal_provider import generar_guia
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


def _ship_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "tracking_number": "2026-DEMO-S-000013",
        "operador": "4-72",
        "guia": "C472-ABCDEF0123",
        "estado": "registrado",
        "destinatario": "Juan Pérez",
        "direccion": "Calle 1 #2-3",
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


def test_generar_guia_deterministic() -> None:
    rid = uuid.uuid4()
    assert generar_guia("4-72", rid) == generar_guia("4-72", rid)
    assert generar_guia("4-72", rid).startswith("C472-")
    assert generar_guia("servientrega", rid).startswith("SE-")


@pytest.mark.asyncio
async def test_create_envio(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    # insert (transaction → fetchrow shipment + execute event); _response → fetch events
    mock_conn.fetchrow.return_value = _ship_row(radicado_id=rid)
    mock_conn.fetch.return_value = [
        {"estado": "registrado", "descripcion": "Envío registrado", "ts": NOW}
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/envios",
        json={"operador": "4-72", "destinatario": "Juan Pérez", "direccion": "Calle 1"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["operador"] == "4-72"
    assert body["estado"] == "registrado"
    assert body["eventos"][0]["estado"] == "registrado"


@pytest.mark.asyncio
async def test_update_estado_valid_transition(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    # resolve_caller_clearance (is_root → sin restricción) → get (registrado) →
    # update_estado (en_transito) → _response events. (D-14: la transición manual
    # ahora resuelve el clearance del llamante antes del get.)
    mock_conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="registrado"),
        _ship_row(id=sid, estado="en_transito"),
    ]
    mock_conn.fetch.return_value = [
        {"estado": "registrado", "descripcion": "x", "ts": NOW},
        {"estado": "en_transito", "descripcion": "En ruta", "ts": NOW},
    ]
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "en_transito", "descripcion": "En ruta"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "en_transito"


@pytest.mark.asyncio
async def test_update_estado_invalid_transition(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _ship_row(id=sid, estado="registrado")
    mock_conn.fetch.return_value = []
    # registrado → entregado no es válido (debe pasar por en_transito)
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "entregado", "acuse_file_id": str(uuid.uuid4()), "acuse_sha256": "a" * 64},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "transicion_invalida"


@pytest.mark.asyncio
async def test_get_envio_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/envios/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# D-14: comprobaciones de autorización (PERM_RADI_SALIDA)
# ---------------------------------------------------------------------------

_USER = str(uuid.uuid4())


@pytest.mark.asyncio
async def test_create_envio_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI_SALIDA requerido para crear envío; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/envios",
        json={"operador": "4-72"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_envio_con_permiso_201(client_with_conn) -> None:
    """PERM_RADI_SALIDA concedido → crear envío devuelve 201."""
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _ship_row(radicado_id=rid)
    mock_conn.fetch.return_value = [
        {"estado": "registrado", "descripcion": "Envío registrado", "ts": NOW}
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/envios",
        json={"operador": "4-72"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "registrado"


@pytest.mark.asyncio
async def test_update_estado_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI_SALIDA requerido para cambiar estado; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/envios/{uuid.uuid4()}/estado",
        json={"estado": "en_transito"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# GET /api/v1/envios — listado transversal paginado (E20, T-12e)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_envios_global_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI_SALIDA requerido para el listado global; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        "/api/v1/envios",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_envios_global_con_permiso_200_total_count(client_with_conn) -> None:
    """Listado global con permiso: 200 + X-Total-Count correcto."""
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    ship = _ship_row(id=sid)
    # fetchval side_effect: check_permission (True) → COUNT(*) (3)
    mock_conn.fetchval.side_effect = [True, 3]
    mock_conn.fetch.side_effect = [
        [ship],  # list_all_paginated rows
        [{"estado": "registrado", "descripcion": "Envío registrado", "ts": NOW}],  # events
    ]
    resp = await client.get(
        "/api/v1/envios?page=1&size=10",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "3"
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_list_envios_global_filtro_estado(client_with_conn) -> None:
    """?estado=entregado filtra por estado y devuelve X-Total-Count correcto."""
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    ship = _ship_row(id=sid, estado="entregado")
    mock_conn.fetchval.side_effect = [True, 1]  # permission + COUNT filtrado
    mock_conn.fetch.side_effect = [
        [ship],  # rows filtradas por estado=entregado
        [{"estado": "entregado", "descripcion": "Entregado al destinatario", "ts": NOW}],
    ]
    resp = await client.get(
        "/api/v1/envios?estado=entregado",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "1"
    body = resp.json()
    assert len(body) == 1
    assert body[0]["estado"] == "entregado"


# ---------------------------------------------------------------------------
# RF-SEG-08: no-read-up — envíos de radicados CLASIFICADOS ocultos a PUBLICA
#
# resolve_caller_clearance usa fetchrow (no fetchval); fetchrow.return_value = None
# → usuario no encontrado → fail-closed a PUBLICA (1). Las consultas del
# repositorio reciben clearance=1, el JOIN r.nivel_seguridad <= 1 filtra las filas
# de radicados CLASIFICADOS (nivel 3) y el mock devuelve vacío / None.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_envios_global_no_read_up(client_with_conn) -> None:
    """Listado transversal: PUBLICA clearance → envíos CLASIFICADOS excluidos; count=0."""
    client, mock_conn = client_with_conn
    # fetchval: permission (True) + COUNT filtrado (0, sin filas visibles)
    mock_conn.fetchval.side_effect = [True, 0]
    mock_conn.fetchrow.return_value = None  # resolve_caller_clearance → PUBLICA (1)
    mock_conn.fetch.return_value = []       # list_all_paginated rows → filtrado
    resp = await client.get(
        "/api/v1/envios",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "0"
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_by_radicado_no_read_up(client_with_conn) -> None:
    """Por radicado: PUBLICA clearance → envío de radicado CLASIFICADO excluido."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permission granted
    mock_conn.fetchrow.return_value = None  # resolve_caller_clearance → PUBLICA (1)
    mock_conn.fetch.return_value = []       # list_by_radicado → filtrado por clearance
    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/envios",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_envio_no_read_up_404(client_with_conn) -> None:
    """Detalle: GET envio cuyo radicado es CLASIFICADO → 404 para llamante PUBLICA."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permission granted
    # fetchrow=None: resolve_caller_clearance → PUBLICA (1);
    # repo.get(shipment_id, clearance=1) → JOIN filtra fila → None → 404.
    mock_conn.fetchrow.return_value = None
    resp = await client.get(
        f"/api/v1/envios/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "envio_not_found"


def test_la_consulta_de_envios_trae_el_numero_de_radicado() -> None:
    """La pantalla de envíos mostraba «Radicado: 43632304-cdea-…».

    Un envío postal lo despacha una persona que tiene el documento en la mano:
    el UUID no lo puede cotejar con nada, y la planilla que se imprime para el
    operador postal llevaba ese mismo identificador en la columna del radicado.
    El dato estaba a un paso — las CUATRO consultas del repositorio ya hacen
    JOIN a `radicados` por el no-read-up (RF-SEG-08) y no se llevaban la
    columna.

    Se comprueba el SQL QUE SE ENVÍA, no el esquema. La primera versión de este
    test construía un `ShipmentResponse` a partir de un diccionario escrito a
    mano: pasaba igual de verde con la columna quitada del SELECT, porque el
    dato lo ponía el propio test. Quitar `r.tracking_number` del `_COLS_S` debe
    poner esto en rojo, y es lo único que lo garantiza.
    """
    from app.repositories.postal_repository import _COLS_S

    assert "r.tracking_number" in _COLS_S


@pytest.mark.asyncio
async def test_get_envio_devuelve_el_numero_que_dio_la_consulta(client_with_conn) -> None:
    """Y el número que sale es el de la FILA, no uno inventado por el servicio.

    Se mira además el SQL que recibió la conexión: es la consulta real la que
    tiene que traer la columna, no el diccionario del test.
    """
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, tracking_number="2026-DEMO-S-000013"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/envios/{sid}", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["tracking_number"] == "2026-DEMO-S-000013"
    sql = mock_conn.fetchrow.await_args.args[0]
    assert "JOIN radicados r" in sql
    assert "r.tracking_number" in sql


@pytest.mark.asyncio
async def test_la_devolucion_manual_guarda_la_causal_en_su_campo(client_with_conn) -> None:
    """La causal iba solo dentro del texto libre de `descripcion`.

    El webhook del operador postal SÍ alimentaba `motivo_devolucion`; la
    devolución que registra una persona desde la pantalla, no. El mismo hecho
    por dos caminos, con distinta calidad de dato — y el peor era el manual, que
    es el habitual: sin causal en su campo no hay forma de reportar por qué se
    devuelven los envíos, aunque el dato se haya elegido de una lista.
    """
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="en_transito"),
        _ship_row(id=sid, estado="devuelto", motivo_devolucion="Postal: no reclamado"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={
            "estado": "devuelto",
            "descripcion": "Causal: Postal: no reclamado — 30 días",
            "motivo": "Postal: no reclamado",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    # La causal viaja como PARÁMETRO del UPDATE, no solo dentro del texto.
    llamadas = [c.args for c in mock_conn.fetchrow.await_args_list]
    actualizacion = next(a for a in llamadas if "UPDATE postal_shipment" in str(a[0]))
    assert "Postal: no reclamado" in actualizacion


@pytest.mark.asyncio
async def test_un_cambio_de_estado_sin_causal_no_borra_la_anterior(client_with_conn) -> None:
    """Gemelo: `motivo` ausente no puede pisar con NULL una causal ya registrada.

    Lo garantiza el COALESCE del repositorio; aquí se fija que el servicio sigue
    pasando `None` en vez de inventarse una cadena vacía, que sí la borraría.
    """
    client, mock_conn = client_with_conn
    sid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="registrado"),
        _ship_row(id=sid, estado="en_transito"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "en_transito"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    llamadas = [c.args for c in mock_conn.fetchrow.await_args_list]
    actualizacion = next(a for a in llamadas if "UPDATE postal_shipment" in str(a[0]))
    # args: (sql, id, estado, motivo, estado_previo, clearance)
    assert actualizacion[3] is None
