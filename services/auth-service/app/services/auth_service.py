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
import time

import asyncpg
import httpx
from jwt import PyJWTError
from orpycamcp_common.tenant import (
    EstadoEntidad,
    estado_entidad,
    slug_valido,
    tenant_schema,
    veredicto_entidad,
)

from app.core.config import settings
from app.core.security import validate_token
from app.repositories.rbac_repository import RBACRepository
from app.schemas.auth import TokenResponse, UserInfoResponse, ValidateResponse

# Tenant slug regex (same rules enforced by tenant-service).

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

    @staticmethod
    async def _exigir_entidad_operativa(
        conn: asyncpg.Connection, access_token: str
    ) -> None:
        """No se entrega un token de una entidad que no se atiende (ADR-035, B9-N4).

        Se mira la entidad que el TOKEN lleva en su claim (la que el gateway verá), no
        la del cuerpo. Sin entidad (superadmin global) no hay nada que comprobar.
        Fail-closed: si el registro no responde, 503 `tenant_estado_no_disponible`.
        """
        claims = await validate_token(access_token)
        slug = claims.get("tenant_slug") or ""
        if not slug:
            return
        veto = await veredicto_entidad(conn, slug)
        if veto is not None:
            raise KeycloakError(veto[0], veto[1])

    async def _exigir_entidad_operativa_o_revocar(
        self, conn: asyncpg.Connection, payload: dict
    ) -> None:
        """Veto de entidad no operativa que, al rechazar, REVOCA la sesion recien emitida.

        Keycloak ya emitio el par de tokens antes de que se consulte el estado de la
        entidad; rechazar sin mas dejaba la sesion (y su refresh token) viva en el
        realm. Se revoca por el endpoint de logout, best-effort: un fallo al revocar
        se registra pero no sustituye al rechazo. Es defensa en PROFUNDIDAD: la
        barrera real es el gateway (vetar por el slug del claim en cada peticion) y
        la via PKCE del front no pasa por este servicio (ADR-035).
        """
        try:
            await self._exigir_entidad_operativa(conn, payload["access_token"])
        except KeycloakError:
            try:
                await self.logout(payload["refresh_token"])
            except Exception:  # noqa: BLE001 — revocar es best-effort; el rechazo manda
                logger.warning("no se pudo revocar la sesion de una entidad no operativa", exc_info=True)
            raise

    async def login(
        self, username: str, password: str, tenant_slug: str, *, conn: asyncpg.Connection
    ) -> TokenResponse:
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

        await self._exigir_entidad_operativa_o_revocar(conn, payload)

        return TokenResponse(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            token_type="Bearer",
            expires_in=payload.get("expires_in", 300),
        )

    async def refresh(self, refresh_token: str, *, conn: asyncpg.Connection) -> TokenResponse:
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
        await self._exigir_entidad_operativa_o_revocar(conn, payload)
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

    async def validate(
        self,
        token: str,
        conn: asyncpg.Connection | None = None,
    ) -> ValidateResponse:
        """Validate JWT and resolve the caller's internal auth_users.id.

        When ``conn`` is provided and the token carries a ``tenant_slug`` claim,
        the method switches the connection's ``search_path`` to the tenant schema,
        looks up (or lazy-provisions) the ``auth_users`` row by ``keycloak_sub``,
        and returns the internal UUID as ``user_id``.

        Fallback to the raw Keycloak ``sub`` when:
        - no ``tenant_slug`` in the token (e.g. superadmin without a tenant), or
        - the tenant schema is not yet provisioned (``UndefinedTableError``), or
        - any other DB error (fail-open: superadmin path is unblocked).
        """
        try:
            claims = await validate_token(token)

            keycloak_sub: str = claims["user_id"]          # raw Keycloak sub
            username: str = claims.get("username", "")
            email: str = claims.get("email", "")
            tenant_slug: str | None = claims.get("tenant_slug")

            # Resolve internal auth_users.id (tenant-aware, lazy provisioning).
            user_id: str = keycloak_sub                    # default: fall back to sub

            # ADR-035: el estado de la entidad va ANTES de tocar su schema. Una
            # entidad no operativa no aprovisiona `auth_users` (el alta perezosa
            # crearía usuarios dentro de una entidad suspendida); el veredicto de
            # servir o no lo toma el api-gateway con este campo.
            tenant_estado: str | None = None
            if tenant_slug:
                tenant_estado = await self._estado_de_la_entidad(conn, tenant_slug)

            if (
                tenant_slug
                and conn is not None
                and tenant_estado == EstadoEntidad.OPERATIVA
                and slug_valido(tenant_slug)
            ):
                schema = tenant_schema(tenant_slug)
                try:
                    await conn.execute(f'SET search_path = "{schema}"')
                    repo = RBACRepository(conn)
                    user = await repo.get_user_by_keycloak_sub(keycloak_sub)
                    if user is None:
                        user = await repo.upsert_user_by_keycloak_sub(
                            keycloak_sub=keycloak_sub,
                            username=username,
                            email=email,
                        )
                    if user is not None:
                        user_id = str(user["id"])
                except (asyncpg.UndefinedTableError, asyncpg.UndefinedSchemaError):
                    # Tenant schema not provisioned yet (RBAC tables absent).
                    # Fail-closed downstream: the raw sub matches no auth_users.id.
                    logger.warning(
                        "validate: tenant '%s' not provisioned (RBAC tables absent); "
                        "falling back to keycloak_sub for user_id",
                        tenant_slug,
                    )
                except asyncpg.UniqueViolationError:
                    # Lazy provisioning hit a unique constraint other than keycloak_sub
                    # (typically a username collision with a pre-existing local user).
                    # Surfaced explicitly: otherwise the user is silently locked out.
                    logger.warning(
                        "validate: lazy provisioning conflict for sub=%s in tenant '%s' "
                        "(likely username collision); falling back to keycloak_sub",
                        keycloak_sub,
                        tenant_slug,
                    )
                except Exception:
                    # Unexpected error — keep auth available (fail-closed downstream)
                    # but make it visible instead of masking it at debug level.
                    logger.exception(
                        "validate: unexpected error resolving internal user_id "
                        "for tenant '%s'; falling back to keycloak_sub",
                        tenant_slug,
                    )
                finally:
                    try:
                        await conn.execute("SET search_path = public")
                    except Exception:
                        pass

            return ValidateResponse(
                valid=True,
                user_id=user_id,
                username=username,
                tenant_slug=tenant_slug,
                roles=claims.get("roles", []),
                expires_in=max(0, int(claims["exp"]) - int(time.time())),
                tenant_estado=tenant_estado,
            )
        except PyJWTError:
            return ValidateResponse(valid=False)

    @staticmethod
    async def _estado_de_la_entidad(conn: asyncpg.Connection | None, slug: str) -> str:
        """Estado de la entidad; `desconocido` si el registro no responde (no se lanza)."""
        if conn is None:
            return "desconocido"
        try:
            return str(await estado_entidad(conn, slug))
        except Exception:
            logger.exception("validate: no se pudo leer el estado de la entidad '%s'", slug)
            return "desconocido"

    async def get_current_user(self, token: str) -> UserInfoResponse:
        try:
            claims = await validate_token(token)
        except PyJWTError:
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
