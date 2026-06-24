# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de clasificación de seguridad y clearance (E08, RF-SEG-08)."""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
_USER = uuid.uuid4()


def _caller(*, is_root: bool = False, admin: bool = False) -> Caller:
    perms = {"USUA_PERM_ADMIN": 5} if admin else {"PERM_RADI": 3}
    return Caller(user_id=_USER, keycloak_sub="kc", is_root=is_root, permissions=perms)


def _override(conn: AsyncMock, caller: Caller) -> None:
    async def _dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_caller] = lambda: caller


@pytest.mark.asyncio
async def test_list_security_levels(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"code": 1, "nombre": "PUBLICA"},
        {"code": 2, "nombre": "RESERVADA"},
        {"code": 3, "nombre": "CLASIFICADA"},
    ]
    _override(conn, _caller())

    resp = await client.get("/api/v1/auth/security-levels", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert [lv["nombre"] for lv in resp.json()] == ["PUBLICA", "RESERVADA", "CLASIFICADA"]


@pytest.mark.asyncio
async def test_clearance_check_allowed(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = 2  # caller_max_level = RESERVADA
    _override(conn, _caller())

    resp = await client.get("/api/v1/auth/clearance/check?level=2", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is True and body["max_level"] == 2


@pytest.mark.asyncio
async def test_clearance_check_denied(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # solo PUBLICA
    _override(conn, _caller())

    resp = await client.get("/api/v1/auth/clearance/check?level=3", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False


@pytest.mark.asyncio
async def test_clearance_root_accesses_all(client: AsyncClient) -> None:
    conn = AsyncMock()
    _override(conn, _caller(is_root=True))

    resp = await client.get("/api/v1/auth/clearance", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_level"] == 3
    assert body["accessible_levels"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_set_group_clearance(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # level_exists → existe
    conn.execute.return_value = None
    _override(conn, _caller(admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_set_group_clearance_denied_without_admin(client: AsyncClient) -> None:
    conn = AsyncMock()
    _override(conn, _caller(admin=False))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
