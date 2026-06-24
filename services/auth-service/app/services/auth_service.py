# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
AuthService — orchestrates Keycloak token flows.

Keycloak is the authority for authentication.  This service acts as a thin
adapter: it forwards credentials, parses responses, and delegates JWT
validation to ``core.security``.

Why use ``api-gateway`` client credentials for ROPC?
Keycloak requires the client to have ``directAccessGrantsEnabled=true``.  The
``auth-service`` client is ``bearerOnly=true`` (it only validates tokens, never
issues them).  The ``api-gateway`` client has the grants flag enabled, so we
use its credentials to perform the password grant on behalf of the caller.
"""

import logging

import httpx
from jose import JWTError

from app.core.config import settings
from app.core.security import validate_token
from app.schemas.auth import TokenResponse, UserInfoResponse, ValidateResponse

logger = logging.getLogger(__name__)


class KeycloakError(Exception):
    """Raised when Keycloak returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class AuthService:
    def __init__(self) -> None:
        # A single shared async client is more efficient than creating one per
        # request, but callers must not close it.  We instantiate it here and
        # the app lifespan manages teardown via ``close()``.
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Token issuance
    # ------------------------------------------------------------------

    async def login(self, username: str, password: str, tenant_slug: str) -> TokenResponse:
        """Resource Owner Password Credentials grant via the api-gateway client."""
        data = {
            "grant_type": "password",
            "client_id": settings.keycloak_gateway_client_id,
            "client_secret": settings.keycloak_gateway_client_secret,
            "username": username,
            "password": password,
            "scope": "openid profile email",
        }
        response = await self._client.post(settings.keycloak_token_url, data=data)

        if response.status_code == 401:
            raise KeycloakError(401, "invalid_credentials")
        if response.status_code != 200:
            logger.error("Keycloak login error %s: %s", response.status_code, response.text)
            raise KeycloakError(response.status_code, "keycloak_error")

        payload = response.json()

        # Verify that the token actually belongs to the claimed tenant.
        # This prevents a user from logging in with someone else's tenant_slug.
        claims = await validate_token(payload["access_token"])
        token_tenant = claims.get("tenant_slug")

        # superadmin users have no tenant restriction
        if "superadmin" not in claims.get("roles", []):
            if token_tenant != tenant_slug:
                raise KeycloakError(403, "tenant_mismatch")

        return TokenResponse(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            token_type="Bearer",
            expires_in=payload.get("expires_in", 300),
        )

    async def refresh(self, refresh_token: str) -> TokenResponse:
        data = {
            "grant_type": "refresh_token",
            "client_id": settings.keycloak_gateway_client_id,
            "client_secret": settings.keycloak_gateway_client_secret,
            "refresh_token": refresh_token,
        }
        response = await self._client.post(settings.keycloak_token_url, data=data)

        if response.status_code == 400:
            raise KeycloakError(401, "invalid_refresh_token")
        if response.status_code != 200:
            logger.error("Keycloak refresh error %s: %s", response.status_code, response.text)
            raise KeycloakError(response.status_code, "keycloak_error")

        payload = response.json()
        return TokenResponse(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            token_type="Bearer",
            expires_in=payload.get("expires_in", 300),
        )

    async def logout(self, refresh_token: str) -> None:
        data = {
            "client_id": settings.keycloak_gateway_client_id,
            "client_secret": settings.keycloak_gateway_client_secret,
            "refresh_token": refresh_token,
        }
        response = await self._client.post(settings.keycloak_logout_url, data=data)

        # Keycloak returns 204 on success, 400 if token is already invalid.
        # Either way the session is gone — treat both as success.
        if response.status_code not in (200, 204, 400):
            logger.error("Keycloak logout error %s: %s", response.status_code, response.text)
            raise KeycloakError(response.status_code, "keycloak_error")

    # ------------------------------------------------------------------
    # Token validation (used by api-gateway)
    # ------------------------------------------------------------------

    async def validate(self, token: str) -> ValidateResponse:
        try:
            claims = await validate_token(token)
            return ValidateResponse(
                valid=True,
                user_id=claims["user_id"],
                username=claims["username"],
                tenant_slug=claims.get("tenant_slug"),
                roles=claims.get("roles", []),
            )
        except JWTError:
            return ValidateResponse(valid=False)

    async def get_current_user(self, token: str) -> UserInfoResponse:
        try:
            claims = await validate_token(token)
        except JWTError:
            raise KeycloakError(401, "invalid_token")

        return UserInfoResponse(
            user_id=claims["user_id"],
            username=claims["username"],
            email=claims.get("email", ""),
            tenant_slug=claims.get("tenant_slug"),
            roles=claims.get("roles", []),
        )

    # ------------------------------------------------------------------
    # Health probe
    # ------------------------------------------------------------------

    async def keycloak_healthy(self) -> bool:
        """Ping the Keycloak realm endpoint to verify connectivity."""
        try:
            response = await self._client.get(settings.keycloak_realm_url, timeout=3.0)
            return response.status_code == 200
        except httpx.RequestError:
            return False
