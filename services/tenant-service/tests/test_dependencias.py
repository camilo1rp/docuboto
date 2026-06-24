# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del CRUD de dependencias (E14). Mockean get_tenant_conn (sin BD real).

asyncpg.Record se sustituye por dict: el código usa dict(r) y r["col"], compatible.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app


def _dep(
    *,
    codigo: int = 100,
    sigla: str = "DG",
    nombre: str = "Dirección General",
    parent_id: uuid.UUID | None = None,
    dep_id: uuid.UUID | None = None,
    depe_rad_tp2: bool = False,
    activo: bool = True,
) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": dep_id or uuid.uuid4(),
        "parent_id": parent_id,
        "codigo": codigo,
        "sigla": sigla,
        "nombre": nombre,
        "depe_rad_tp2": depe_rad_tp2,
        "activo": activo,
        "created_at": now,
        "updated_at": now,
    }


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.execute.return_value = "DELETE 0"

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_dependencia(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _dep(codigo=100, sigla="DG")
    resp = await client.post(
        "/api/v1/dependencias", json={"codigo": 100, "sigla": "DG", "nombre": "Dirección General"}
    )
    assert resp.status_code == 201
    assert resp.json()["codigo"] == 100


@pytest.mark.asyncio
async def test_create_duplicate_codigo(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    resp = await client.post(
        "/api/v1/dependencias", json={"codigo": 100, "sigla": "DG", "nombre": "X"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_bad_parent(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.ForeignKeyViolationError("fk")
    resp = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 200, "sigla": "SUB", "nombre": "Sub", "parent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_parent"


@pytest.mark.asyncio
async def test_get_by_codigo_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _dep(codigo=305, sigla="ARCH")
    resp = await client.get("/api/v1/dependencias/by-codigo/305")
    assert resp.status_code == 200
    assert resp.json()["sigla"] == "ARCH"


@pytest.mark.asyncio
async def test_get_by_codigo_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.get("/api/v1/dependencias/by-codigo/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_by_id_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/dependencias/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tree_builds_hierarchy(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    root_id = uuid.uuid4()
    child_id = uuid.uuid4()
    conn.fetch.return_value = [
        _dep(codigo=100, sigla="DG", dep_id=root_id),
        _dep(codigo=110, sigla="SUB", dep_id=child_id, parent_id=root_id),
    ]
    resp = await client.get("/api/v1/dependencias/tree")
    assert resp.status_code == 200
    tree = resp.json()
    assert len(tree) == 1
    assert tree[0]["codigo"] == 100
    assert len(tree[0]["children"]) == 1
    assert tree[0]["children"][0]["codigo"] == 110


@pytest.mark.asyncio
async def test_update_partial(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, nombre="Nuevo Nombre")
    resp = await client.patch(f"/api/v1/dependencias/{dep_id}", json={"nombre": "Nuevo Nombre"})
    assert resp.status_code == 200
    assert resp.json()["nombre"] == "Nuevo Nombre"


@pytest.mark.asyncio
async def test_update_self_parent_rejected(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    dep_id = uuid.uuid4()
    resp = await client.patch(f"/api/v1/dependencias/{dep_id}", json={"parent_id": str(dep_id)})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.patch(f"/api/v1/dependencias/{uuid.uuid4()}", json={"nombre": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_with_children_conflict(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = 1  # has_children → True
    resp = await client.delete(f"/api/v1/dependencias/{uuid.uuid4()}")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_ok(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = None  # no children
    conn.execute.return_value = "DELETE 1"
    resp = await client.delete(f"/api/v1/dependencias/{uuid.uuid4()}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = None
    conn.execute.return_value = "DELETE 0"
    resp = await client.delete(f"/api/v1/dependencias/{uuid.uuid4()}")
    assert resp.status_code == 404
