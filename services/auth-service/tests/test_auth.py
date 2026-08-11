# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for auth-service endpoints.

Keycloak is mocked entirely — these tests verify the FastAPI layer, schema
validation, audit logging, and correct error mapping without requiring a live
Keycloak instance.
"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.database import get_optional_tenant_conn
from app.main import app
from tests.conftest import FAKE_TOKEN_RESPONSE


def _override_optional_conn(conn: AsyncMock | None) -> None:
    """Override get_optional_tenant_conn for tests that exercise the /me permissions path."""

    async def _dep() -> AsyncGenerator[AsyncMock | None, None]:
        yield conn

    app.dependency_overrides[get_optional_tenant_conn] = _dep

# ---------------------------------------------------------------------------
# POST /api/v1/auth/token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_success(client: AsyncClient) -> None:
    """Successful ROPC login returns a token pair."""
    with (
        patch(
            "app.services.auth_service.AuthService.login",
            new_callable=AsyncMock,
        ) as mock_login,
    ):
        from app.schemas.auth import TokenResponse

        mock_login.return_value = TokenResponse(**FAKE_TOKEN_RESPONSE)

        response = await client.post(
            "/api/v1/auth/token",
            json={"username": "operador", "password": "orpycamcp_dev", "tenant_slug": "demo"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300


@pytest.mark.asyncio
async def test_token_invalid_credentials(client: AsyncClient) -> None:
    """Wrong password returns 401 with structured error."""
    from app.services.auth_service import KeycloakError

    with patch(
        "app.services.auth_service.AuthService.login",
        new_callable=AsyncMock,
        side_effect=KeycloakError(401, "invalid_credentials"),
    ):
        response = await client.post(
            "/api/v1/auth/token",
            json={"username": "operador", "password": "wrong", "tenant_slug": "demo"},
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"]["error"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_token_tenant_mismatch(client: AsyncClient) -> None:
    """A user logging in with a tenant they don't belong to returns 403."""
    from app.services.auth_service import KeycloakError

    with patch(
        "app.services.auth_service.AuthService.login",
        new_callable=AsyncMock,
        side_effect=KeycloakError(403, "tenant_mismatch"),
    ):
        response = await client.post(
            "/api/v1/auth/token",
            json={"username": "operador", "password": "orpycamcp_dev", "tenant_slug": "other"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "tenant_mismatch"


# ---------------------------------------------------------------------------
# POST /api/v1/auth/refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token(client: AsyncClient) -> None:
    """Valid refresh token returns a new token pair."""
    from app.schemas.auth import TokenResponse

    with patch(
        "app.services.auth_service.AuthService.refresh",
        new_callable=AsyncMock,
        return_value=TokenResponse(**FAKE_TOKEN_RESPONSE),
    ):
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "eyFAKE_REFRESH_TOKEN"},
        )

    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_refresh_invalid_token(client: AsyncClient) -> None:
    """Expired or invalid refresh token returns 401."""
    from app.services.auth_service import KeycloakError

    with patch(
        "app.services.auth_service.AuthService.refresh",
        new_callable=AsyncMock,
        side_effect=KeycloakError(401, "invalid_refresh_token"),
    ):
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "expired-token"},
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/auth/logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout(client: AsyncClient) -> None:
    """Logout returns 204 and invalidates the token in Keycloak."""
    with patch(
        "app.services.auth_service.AuthService.logout",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "eyFAKE_REFRESH_TOKEN"},
        )

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /api/v1/auth/validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_valid_token(client: AsyncClient) -> None:
    """api-gateway calls /validate with a valid token and gets user info back."""
    from app.schemas.auth import ValidateResponse

    expected = ValidateResponse(
        valid=True,
        user_id="some-uuid",
        username="operador",
        tenant_slug="demo",
        roles=["operator"],
    )

    with patch(
        "app.services.auth_service.AuthService.validate",
        new_callable=AsyncMock,
        return_value=expected,
    ):
        response = await client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "Bearer eyFAKE_ACCESS_TOKEN"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["username"] == "operador"
    assert body["tenant_slug"] == "demo"
    assert "operator" in body["roles"]


@pytest.mark.asyncio
async def test_validate_expired_token(client: AsyncClient) -> None:
    """An expired token returns valid=False (not an HTTP error — gateway decides)."""
    from app.schemas.auth import ValidateResponse

    with patch(
        "app.services.auth_service.AuthService.validate",
        new_callable=AsyncMock,
        return_value=ValidateResponse(valid=False),
    ):
        response = await client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "Bearer expired.token.here"},
        )

    assert response.status_code == 200
    assert response.json()["valid"] is False


@pytest.mark.asyncio
async def test_validate_missing_header(client: AsyncClient) -> None:
    """Missing Authorization header returns 401."""
    response = await client.get("/api/v1/auth/validate")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing_token"


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_returns_user_info(client: AsyncClient) -> None:
    """Authenticated user gets their own info from the token claims.

    No X-Tenant-Slug → get_optional_tenant_conn yields None → permissions=[].
    """
    from app.schemas.auth import UserInfoResponse

    expected = UserInfoResponse(
        user_id="some-uuid",
        username="operador",
        email="operador@demo.orpycamcp.local",
        tenant_slug="demo",
        roles=["operator"],
    )

    with patch(
        "app.services.auth_service.AuthService.get_current_user",
        new_callable=AsyncMock,
        return_value=expected,
    ):
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
    from app.schemas.auth import UserInfoResponse

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
    # get_user_by_keycloak_sub → user exists
    conn.fetchrow.return_value = {
        "id": user_id,
        "username": "OPERADOR",
        "email": "operador@demo.orpycamcp.local",
        "keycloak_sub": sub,
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

    with patch(
        "app.services.auth_service.AuthService.get_current_user",
        new_callable=AsyncMock,
        return_value=user_info,
    ):
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
    from app.schemas.auth import UserInfoResponse

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
        "keycloak_sub": sub,
        "is_root": False,
        "is_active": True,
    }
    # USUA_PERM_EXPEDIENTE granted only at read level (crud=1), NOT write (3).
    conn.fetch.return_value = [{"nombre": "USUA_PERM_EXPEDIENTE", "crud": 1}]
    conn.fetchval.return_value = False
    _override_optional_conn(conn)

    with patch(
        "app.services.auth_service.AuthService.get_current_user",
        new_callable=AsyncMock,
        return_value=user_info,
    ):
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
    from app.schemas.auth import UserInfoResponse

    user_info = UserInfoResponse(
        user_id="ghost-sub",
        username="ghost",
        email="ghost@demo.test",
        tenant_slug="demo",
        roles=[],
    )

    conn = AsyncMock()
    # get_user_by_keycloak_sub → user NOT provisioned in tenant
    conn.fetchrow.return_value = None
    _override_optional_conn(conn)

    with patch(
        "app.services.auth_service.AuthService.get_current_user",
        new_callable=AsyncMock,
        return_value=user_info,
    ):
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
    from app.schemas.auth import UserInfoResponse

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
        "keycloak_sub": sub,
        "is_root": True,
        "is_active": True,
    }
    # resolve_permissions returns normal perms; is_root=True embeds USUA_PERM_ROOT
    conn.fetch.return_value = [{"nombre": "PERM_RADI", "crud": 5}]
    conn.fetchval.return_value = True  # is_root
    _override_optional_conn(conn)

    with patch(
        "app.services.auth_service.AuthService.get_current_user",
        new_callable=AsyncMock,
        return_value=user_info,
    ):
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
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_es_vivacidad_y_no_llama_a_keycloak(client: AsyncClient) -> None:
    """`/health` gobierna los reinicios del contenedor: no consulta a nadie.

    Antes tomaba una conexión del pool Y preguntaba a Keycloak en cada sondeo
    (cada 15 s). Reiniciar auth-service porque Keycloak está caído no arregla
    Keycloak: tira el trabajo en vuelo y encadena reinicios sobre un incidente
    que ya estaba en curso.
    """
    with patch(
        "app.services.auth_service.AuthService.keycloak_healthy",
        new_callable=AsyncMock,
        return_value=True,
    ) as kc:
        response = await client.get("/health")

    assert kc.await_count == 0
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "auth-service"}


@pytest.mark.asyncio
async def test_ready_con_keycloak_caido_devuelve_503(client: AsyncClient) -> None:
    """Keycloak SÍ es bloqueante en la disponibilidad: sin él no se autentica.

    El 503 saca el servicio del reparto del balanceador **sin reiniciarlo**, que
    es exactamente la distinción que este bloque introduce.
    """
    with patch(
        "app.services.auth_service.AuthService.keycloak_healthy",
        new_callable=AsyncMock,
        return_value=False,
    ):
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["keycloak"] == "error"
    assert response.json()["status"] == "not_ready"
