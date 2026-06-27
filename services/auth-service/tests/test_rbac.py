# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del RBAC (E08): autorización pura + endpoints con conexión mockeada."""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app
from app.services.rbac_service import has_permission

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
# El llamante de las pruebas administra RBAC (USUA_PERM_ADMIN); el gate (ADR-013) se
# resuelve normalmente vía BD, pero aquí se sobreescribe para aislar el endpoint.
_ADMIN_CALLER = Caller(
    user_id=uuid.uuid4(),
    keycloak_sub="kc-admin",
    is_root=True,
    permissions={"USUA_PERM_ADMIN": 5},
)


# --- autorización pura (sin BD) --------------------------------------------
def test_has_permission_root_bypass() -> None:
    perms = {"USUA_PERM_ROOT": 5}
    assert has_permission(perms, "PERM_RADI", min_crud=3) is True


def test_has_permission_min_crud() -> None:
    perms = {"PERM_RADI": 2}
    assert has_permission(perms, "PERM_RADI", min_crud=2) is True
    assert has_permission(perms, "PERM_RADI", min_crud=3) is False


def test_has_permission_absent() -> None:
    assert has_permission({}, "PERM_RADI") is False


# --- endpoints con conexión mockeada ---------------------------------------
def _override_conn(conn: AsyncMock) -> None:
    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    # El gate de autorización se aísla: el endpoint queda probado sin la resolución
    # de permisos del llamante (cubierta por sus propios tests).
    app.dependency_overrides[get_caller] = lambda: _ADMIN_CALLER


@pytest.mark.asyncio
async def test_create_user(client: AsyncClient) -> None:
    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": new_id,
        "username": "JPEREZ",
        "email": "j@x.co",
        "keycloak_sub": None,
        "is_root": False,
        "is_active": True,
    }
    _override_conn(conn)

    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "jperez", "email": "j@x.co"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "JPEREZ"
    assert body["id"] == str(new_id)


@pytest.mark.asyncio
async def test_missing_tenant_header_is_400(client: AsyncClient) -> None:
    # Sin override: get_tenant_conn valida la cabecera y debe rechazar.
    app.dependency_overrides.pop(get_tenant_conn, None)
    resp = await client.get("/api/v1/auth/groups")  # sin X-Tenant-Slug
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_tenant"


@pytest.mark.asyncio
async def test_list_permissions(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": uuid.uuid4(), "nombre": "PERM_RADI", "descripcion": "base"},
        {"id": uuid.uuid4(), "nombre": "USUA_PERM_ROOT", "descripcion": "root"},
    ]
    _override_conn(conn)

    resp = await client.get("/api/v1/auth/permissions", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    nombres = [p["nombre"] for p in resp.json()]
    assert "PERM_RADI" in nombres and "USUA_PERM_ROOT" in nombres


@pytest.mark.asyncio
async def test_user_permissions_resolution(client: AsyncClient) -> None:
    uid = uuid.uuid4()
    conn = AsyncMock()
    # get_user → existe; resolve_permissions → fetch; is_root → fetchval
    conn.fetchrow.return_value = {
        "id": uid,
        "username": "JPEREZ",
        "email": None,
        "keycloak_sub": None,
        "is_root": False,
        "is_active": True,
    }
    conn.fetch.return_value = [
        {"nombre": "PERM_RADI", "crud": 3},
        {"nombre": "USUA_PERM_EXPEDIENTE", "crud": 2},
    ]
    conn.fetchval.return_value = False  # is_root
    _override_conn(conn)

    resp = await client.get(f"/api/v1/auth/users/{uid}/permissions", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_root"] is False
    assert body["permissions"]["PERM_RADI"] == 3
    assert body["permissions"]["USUA_PERM_EXPEDIENTE"] == 2


# --- D-10: endpoint ligero para selectores (PERM_RADI, no admin) ------------
def _override_caller(conn: AsyncMock, caller: Caller) -> None:
    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_caller] = lambda: caller


@pytest.mark.asyncio
async def test_list_pickable_users_perm_radi(client: AsyncClient) -> None:
    """Un operador con PERM_RADI (sin admin) obtiene id+username; sin email/PII."""
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": u1, "username": "ANA"},
        {"id": u2, "username": "BETO"},
    ]
    _override_caller(
        conn,
        Caller(user_id=u1, keycloak_sub="kc-op", is_root=False, permissions={"PERM_RADI": 3}),
    )
    resp = await client.get(
        "/api/v1/auth/users/pickable", headers={**TENANT_HEADERS, "X-User-Id": "kc-op"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [u["username"] for u in body] == ["ANA", "BETO"]
    # Proyección mínima: solo id + username, sin email ni metadatos administrativos.
    assert set(body[0].keys()) == {"id", "username"}


@pytest.mark.asyncio
async def test_list_pickable_users_denies_without_perm_radi(client: AsyncClient) -> None:
    """Sin PERM_RADI (ni root), el endpoint pickable responde 403."""
    conn = AsyncMock()
    _override_caller(
        conn,
        Caller(
            user_id=uuid.uuid4(),
            keycloak_sub="kc-nobody",
            is_root=False,
            permissions={"USUA_PERM_CONSULTA": 1},
        ),
    )
    resp = await client.get(
        "/api/v1/auth/users/pickable", headers={**TENANT_HEADERS, "X-User-Id": "kc-nobody"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# --- gate de autorización (ADR-013) ----------------------------------------
@pytest.mark.asyncio
async def test_gate_denies_without_admin(client: AsyncClient) -> None:
    """Un llamante sin USUA_PERM_ADMIN (ni root) recibe 403."""
    conn = AsyncMock()
    _override_conn(conn)
    # Sobreescribe el llamante con uno sin permiso de administración.
    app.dependency_overrides[get_caller] = lambda: Caller(
        user_id=uuid.uuid4(), keycloak_sub="kc-op", is_root=False, permissions={"PERM_RADI": 3}
    )
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "x", "email": "x@x.co"},
        headers={**TENANT_HEADERS, "X-User-Id": "kc-op"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_gate_missing_user_id_is_401(client: AsyncClient) -> None:
    """Sin X-User-Id el gate no puede resolver al llamante → 401."""
    conn = AsyncMock()

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep  # tenant OK; get_caller real
    resp = await client.get("/api/v1/auth/groups", headers=TENANT_HEADERS)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gate_not_provisioned_is_403(client: AsyncClient) -> None:
    """Usuario válido en Keycloak pero sin fila en auth_users → 403."""
    conn = AsyncMock()
    conn.fetchrow.return_value = None  # get_user_by_keycloak_sub → no existe

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    resp = await client.get(
        "/api/v1/auth/groups", headers={**TENANT_HEADERS, "X-User-Id": "kc-ghost"}
    )
    assert resp.status_code == 403
