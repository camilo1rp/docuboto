# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de catálogos de referencia (E14). Mockean get_tenant_conn (sin BD real)."""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app


def _item(codigo: str = "VEN", nombre: str = "Ventanilla", activo: bool = True) -> dict:
    return {"id": uuid.uuid4(), "codigo": codigo, "nombre": nombre, "activo": activo}


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.execute.return_value = "DELETE 0"

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_catalog_names(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.get("/api/v1/catalogos")
    assert resp.status_code == 200
    assert "medios-recepcion" in resp.json()


@pytest.mark.asyncio
async def test_causales_catalog(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """Catálogo de causales (devolución/anulación/reasignación) — paridad legado."""
    client, conn = client_conn
    listed = await client.get("/api/v1/catalogos")
    assert "causales" in listed.json()
    conn.fetch.return_value = [_item(codigo="DEV-DEP", nombre="Otra dependencia")]
    resp = await client.get("/api/v1/catalogos/causales")
    assert resp.status_code == 200
    assert resp.json()[0]["codigo"] == "DEV-DEP"


@pytest.mark.asyncio
async def test_unknown_catalog_404(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.get("/api/v1/catalogos/no-existe")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_catalog"


@pytest.mark.asyncio
async def test_list_items(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetch.return_value = [_item(), _item(codigo="COR", nombre="Correo")]
    resp = await client.get("/api/v1/catalogos/medios-recepcion")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_create_item(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _item(codigo="WHA", nombre="WhatsApp")
    resp = await client.post(
        "/api/v1/catalogos/medios-recepcion", json={"codigo": "WHA", "nombre": "WhatsApp"}
    )
    assert resp.status_code == 201
    assert resp.json()["codigo"] == "WHA"


@pytest.mark.asyncio
async def test_create_duplicate_409(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    resp = await client.post(
        "/api/v1/catalogos/medios-recepcion", json={"codigo": "VEN", "nombre": "X"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_item(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _item(codigo="VEN", nombre="Ventanilla Única", activo=False)
    resp = await client.patch(
        f"/api/v1/catalogos/medios-recepcion/{uuid.uuid4()}",
        json={"nombre": "Ventanilla Única", "activo": False},
    )
    assert resp.status_code == 200
    assert resp.json()["activo"] is False


@pytest.mark.asyncio
async def test_update_item_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.patch(
        f"/api/v1/catalogos/medios-recepcion/{uuid.uuid4()}", json={"nombre": "X"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_item(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 1"
    resp = await client.delete(f"/api/v1/catalogos/tipos-anexo/{uuid.uuid4()}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_item_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 0"
    resp = await client.delete(f"/api/v1/catalogos/tipos-anexo/{uuid.uuid4()}")
    assert resp.status_code == 404
