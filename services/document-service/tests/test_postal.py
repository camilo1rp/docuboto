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
    # get (registrado) → update_estado (en_transito) → _response events
    mock_conn.fetchrow.side_effect = [
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
        json={"estado": "entregado"},
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
