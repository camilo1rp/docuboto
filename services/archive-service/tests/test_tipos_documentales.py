# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del catálogo de tipos documentales (E03, RF-MET-07)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS


def _tipo_row(tipo_id: uuid.UUID, **kw) -> dict:
    base = {
        "id": tipo_id,
        "trd_serie_id": None,
        "code": "RES",
        "nombre": "Resolución",
        "obligatorio": True,
        "metadata_template": "Resolución",
        "activo": True,
        "created_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_tipo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _tipo_row(tid)

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={
            "code": "RES",
            "nombre": "Resolución",
            "obligatorio": True,
            "metadata_template": "Resolución",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "RES"
    assert body["obligatorio"] is True


@pytest.mark.asyncio
async def test_list_tipos(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_tipo_row(uuid.uuid4()), _tipo_row(uuid.uuid4(), code="ACT")]
    resp = await client.get("/api/v1/tipos-documentales?activo=true", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_tipo_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/tipos-documentales/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_tipo_missing_tenant(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
    )
    assert resp.status_code == 400
