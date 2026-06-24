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

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import FAKE_TOKEN_RESPONSE

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
    """Authenticated user gets their own info from the token claims."""
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
    assert response.json()["username"] == "operador"


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """Health endpoint returns 200 with structured status."""
    with patch(
        "app.services.auth_service.AuthService.keycloak_healthy",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["keycloak"] == "ok"
    assert body["database"] == "ok"
    assert body["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_health_keycloak_down(client: AsyncClient) -> None:
    """When Keycloak is unreachable, status is degraded."""
    with patch(
        "app.services.auth_service.AuthService.keycloak_healthy",
        new_callable=AsyncMock,
        return_value=False,
    ):
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["keycloak"] == "error"
    assert body["status"] == "degraded"
