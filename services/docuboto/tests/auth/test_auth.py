# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /api/v1/auth/me`: identidad del portador y permisos efectivos.

La persona llega ya verificada (lo que deja `UsuarioActualMiddleware`); el
emisor y la verificación del token los prueba `test_identidad.py`.
"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from orpycamcp_common.database import get_optional_tenant_conn

from tests.auth.app import app
from tests.auth.fakes_identidad import como


def _override_optional_conn(conn: AsyncMock | None) -> None:
    """Override get_optional_tenant_conn for tests that exercise the /me permissions path."""

    async def _dep() -> AsyncGenerator[AsyncMock | None, None]:
        yield conn

    app.dependency_overrides[get_optional_tenant_conn] = _dep

# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_returns_user_info(client: AsyncClient) -> None:
    """Authenticated user gets their own info from the token claims.

    No X-Tenant-Slug → get_optional_tenant_conn yields None → permissions=[].
    """
    from auth.schemas.auth import UserInfoResponse

    expected = UserInfoResponse(
        user_id="some-uuid",
        username="operador",
        email="operador@demo.orpycamcp.local",
        tenant_slug="demo",
        roles=["operator"],
    )

    with como(expected):
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer eyFAKE_ACCESS_TOKEN"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "operador"
    # No tenant header → fail-closed: empty permissions and levels
    assert body["permissions"] == []
    assert body["permission_levels"] == {}


@pytest.mark.asyncio
async def test_me_permissions_populated(client: AsyncClient) -> None:
    """When the user is provisioned in the tenant, permissions are resolved and returned."""
    from auth.schemas.auth import UserInfoResponse

    sub = str(uuid.uuid4())
    user_id = uuid.uuid4()

    user_info = UserInfoResponse(
        user_id=sub,
        username="operador",
        email="operador@demo.orpycamcp.local",
        tenant_slug="demo",
        roles=["operator"],
    )

    conn = AsyncMock()
    # get_user → user exists
    conn.fetchrow.return_value = {
        "id": user_id,
        "username": "OPERADOR",
        "email": "operador@demo.orpycamcp.local",
        "is_root": False,
        "is_active": True,
    }
    # resolve_permissions → two granted permissions
    conn.fetch.return_value = [
        {"nombre": "PERM_RADI", "crud": 3},
        {"nombre": "USUA_PERM_CONSULTA", "crud": 1},
    ]
    # is_root → False
    conn.fetchval.return_value = False
    _override_optional_conn(conn)

    with como(user_info):
        response = await client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer eyFAKE_ACCESS_TOKEN",
                "X-Tenant-Slug": "demo",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body["permissions"]) == {"PERM_RADI", "USUA_PERM_CONSULTA"}
    # permission_levels carries the CRUD level lost by the flat `permissions`
    # list (D-07 gap): distinguishes read (1) from create/delete (3).
    assert body["permission_levels"] == {"PERM_RADI": 3, "USUA_PERM_CONSULTA": 1}


@pytest.mark.asyncio
async def test_me_permission_levels_distinguish_read_from_write(client: AsyncClient) -> None:
    """Same permission name at different CRUD levels must not collapse to a bool.

    This is the exact bug reported by the UX review: the frontend's `can()`
    checked only presence in `permissions`, so a read-level grant (crud=1) was
    indistinguishable from a write-level grant (crud=3), while backends gate
    with `min_crud=3` (RF-SEG-03) — offering actions that then 403.
    """
    from auth.schemas.auth import UserInfoResponse

    sub = str(uuid.uuid4())
    user_id = uuid.uuid4()

    user_info = UserInfoResponse(
        user_id=sub,
        username="lector",
        email="lector@demo.orpycamcp.local",
        tenant_slug="demo",
        roles=["operator"],
    )

    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": user_id,
        "username": "LECTOR",
        "email": "lector@demo.orpycamcp.local",
        "is_root": False,
        "is_active": True,
    }
    # USUA_PERM_EXPEDIENTE granted only at read level (crud=1), NOT write (3).
    conn.fetch.return_value = [{"nombre": "USUA_PERM_EXPEDIENTE", "crud": 1}]
    conn.fetchval.return_value = False
    _override_optional_conn(conn)

    with como(user_info):
        response = await client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer eyFAKE_ACCESS_TOKEN",
                "X-Tenant-Slug": "demo",
            },
        )

    assert response.status_code == 200
    body = response.json()
    # `permissions` alone (legacy contract) still shows the permission as present.
    assert "USUA_PERM_EXPEDIENTE" in body["permissions"]
    # `permission_levels` reveals it's read-only — insufficient for min_crud=3 actions.
    assert body["permission_levels"]["USUA_PERM_EXPEDIENTE"] == 1
    assert body["permission_levels"]["USUA_PERM_EXPEDIENTE"] < 3


@pytest.mark.asyncio
async def test_me_permissions_empty_when_not_provisioned(client: AsyncClient) -> None:
    """User authenticated via Keycloak but not in tenant DB → permissions=[] (fail-closed)."""
    from auth.schemas.auth import UserInfoResponse

    user_info = UserInfoResponse(
        user_id="ghost-sub",
        username="ghost",
        email="ghost@demo.test",
        tenant_slug="demo",
        roles=[],
    )

    conn = AsyncMock()
    # get_user → user NOT provisioned in tenant
    conn.fetchrow.return_value = None
    _override_optional_conn(conn)

    with como(user_info):
        response = await client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer eyFAKE_ACCESS_TOKEN",
                "X-Tenant-Slug": "demo",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == []
    assert body["permission_levels"] == {}


@pytest.mark.asyncio
async def test_me_root_includes_usua_perm_root(client: AsyncClient) -> None:
    """Root users always have USUA_PERM_ROOT in their permissions list."""
    from auth.schemas.auth import UserInfoResponse

    sub = str(uuid.uuid4())
    user_id = uuid.uuid4()

    user_info = UserInfoResponse(
        user_id=sub,
        username="admin",
        email="admin@demo.test",
        tenant_slug="demo",
        roles=["operator"],
    )

    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": user_id,
        "username": "ADMIN",
        "email": "admin@demo.test",
        "is_root": True,
        "is_active": True,
    }
    # resolve_permissions returns normal perms; is_root=True embeds USUA_PERM_ROOT
    conn.fetch.return_value = [{"nombre": "PERM_RADI", "crud": 5}]
    conn.fetchval.return_value = True  # is_root
    _override_optional_conn(conn)

    with como(user_info):
        response = await client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer eyFAKE_ACCESS_TOKEN",
                "X-Tenant-Slug": "demo",
            },
        )

    assert response.status_code == 200
    body = response.json()
    perms = body["permissions"]
    assert "USUA_PERM_ROOT" in perms
    assert "PERM_RADI" in perms
    # RBACService.resolve_effective_permissions embeds USUA_PERM_ROOT at crud=5.
    assert body["permission_levels"]["USUA_PERM_ROOT"] == 5
    assert body["permission_levels"]["PERM_RADI"] == 5


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me — entidad sin schema (B7, F3)
# ---------------------------------------------------------------------------


class _PoolFalso:
    """Pool mínimo: `acquire()` entrega la conexión doblada."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    def acquire(self):  # noqa: ANN201
        conn = self._conn

        class _Cm:
            async def __aenter__(self) -> AsyncMock:
                return conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Cm()


@pytest.mark.asyncio
async def test_me_entidad_sin_schema_permisos_vacios(client: AsyncClient) -> None:
    """Slug bien formado de una entidad SIN schema: 200 con vacíos, nunca 500.

    Antes el `SET search_path` sobre un schema inexistente no fallaba y la primera
    consulta reventaba con `UndefinedTableError` (500): `/me` es el arranque del
    frontend, así que «entidad aún no lista» se veía como un login roto. Aquí la
    conexión doblada REVIENTA si alguien consulta `auth_users` (como Postgres) y
    responde que el schema no existe: solo el camino con `to_regclass` pasa.
    """
    import asyncpg

    from auth.schemas.auth import UserInfoResponse

    user_info = UserInfoResponse(
        user_id="kc-sub-1",
        username="nuevo",
        email="n@demo.test",
        tenant_slug="sin-schema-b7",
        roles=[],
    )
    conn = AsyncMock()
    conn.fetchval.return_value = False  # to_regclass(...) IS NOT NULL
    conn.fetchrow.side_effect = asyncpg.UndefinedTableError('relation "auth_users" does not exist')
    app.state.db_pool = _PoolFalso(conn)
    try:
        with como(user_info):
            response = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer eyFAKE", "X-Tenant-Slug": "sin-schema-b7"},
            )
    finally:
        del app.state.db_pool

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["permissions"] == []  # no hay usuario interno
    assert body["permissions"] == []
    assert body["permission_levels"] == {}
    conn.fetchrow.assert_not_awaited()
    # Ni siquiera se fija el search_path hacia un schema que no existe.
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_me_entidad_con_schema_sigue_resolviendo_permisos(client: AsyncClient) -> None:
    """Control positivo: con `auth_users` presente se consulta como siempre."""
    from auth.schemas.auth import UserInfoResponse

    user_info = UserInfoResponse(
        user_id="kc-sub-2", username="x", email="x@demo.test", tenant_slug="demo", roles=[]
    )
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None  # usuario no aprovisionado
    app.state.db_pool = _PoolFalso(conn)
    try:
        with como(user_info):
            response = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer eyFAKE", "X-Tenant-Slug": "demo"},
            )
    finally:
        del app.state.db_pool

    assert response.status_code == 200, response.text
    conn.fetchrow.assert_awaited()
    assert any("search_path" in str(c.args[0]) for c in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_me_slug_malformado_sigue_siendo_400(client: AsyncClient) -> None:
    """La validación del slug no se relaja: el 400 es previo a tocar la base."""
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer eyFAKE", "X-Tenant-Slug": 'x"; DROP SCHEMA public;--'},
    )
    assert response.status_code == 400
