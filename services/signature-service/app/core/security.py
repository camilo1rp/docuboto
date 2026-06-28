# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
JWT validation and FastAPI dependency for the signature-service (RF-FIR-11, D-01).

Pattern mirrors auth-service/app/core/security.py:
  - JWKS fetched from Keycloak and cached in memory (TTL = settings.jwks_cache_ttl).
  - Signing key selected by `kid` from the token header.
  - Issuer verified against settings.expected_issuer.
  - `get_current_user` dep rejects requests with no/invalid token (401),
    rejects callers without PERM_FIRMA in their permissions[] claim (403),
    and rejects tokens whose tenant_slug does not match X-Tenant-Slug (403).

ADR-013: PERM_FIRMA is a fine-grained permission, not a realm role.  Keycloak
emits it in a custom `permissions` claim (protocol mapper) rather than in
`realm_access.roles`.  Checking roles[] here is a cross-tenant escalation risk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWKSet

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JWKS in-memory cache
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_jwks_lock = asyncio.Lock()


async def _get_jwks() -> dict[str, Any]:
    """Return cached JWKS, refreshing from Keycloak when TTL has expired."""
    global _jwks_cache, _jwks_fetched_at

    now = time.monotonic()
    if _jwks_cache and (now - _jwks_fetched_at) < settings.jwks_cache_ttl:
        return _jwks_cache

    async with _jwks_lock:
        now = time.monotonic()
        if _jwks_cache and (now - _jwks_fetched_at) < settings.jwks_cache_ttl:
            return _jwks_cache

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(settings.keycloak_jwks_url)
            response.raise_for_status()
            _jwks_cache = response.json()
            _jwks_fetched_at = time.monotonic()
            logger.info("JWKS refreshed from Keycloak")

    return _jwks_cache


def _select_signing_key(jwks: dict[str, Any], token: str) -> Any:
    """Return the public key from the JWKS whose ``kid`` matches the token header."""
    kid = jwt.get_unverified_header(token).get("kid")
    jwk_set = PyJWKSet.from_dict(jwks)
    for key in jwk_set.keys:
        if key.key_id == kid:
            return key.key
    raise InvalidTokenError(f"No signing key in JWKS matches kid={kid!r}")


def _extract_claims(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise raw JWT payload into the claims shape the platform uses.

    `roles` comes from realm_access (realm-level RBAC).
    `permissions` comes from a top-level `permissions` claim emitted by a
    Keycloak custom protocol mapper — these are the fine-grained ACL flags
    (ADR-013) such as PERM_FIRMA, RADI_CREAR, etc.
    """
    realm_access = payload.get("realm_access", {})
    roles: list[str] = realm_access.get("roles", [])
    permissions: list[str] = payload.get("permissions", [])
    tenant_slug: str | None = payload.get("tenant_slug")

    return {
        "user_id": payload.get("sub", ""),
        "username": payload.get("preferred_username", ""),
        "email": payload.get("email", ""),
        "tenant_slug": tenant_slug,
        "roles": roles,
        "permissions": permissions,
        "exp": payload.get("exp"),
        "iat": payload.get("iat"),
    }


async def validate_token(token: str) -> dict[str, Any]:
    """
    Validate a Bearer JWT against Keycloak's public JWKS.

    Returns normalised claims on success.
    Raises ``PyJWTError`` on any validation failure.
    """
    jwks = await _get_jwks()
    signing_key = _select_signing_key(jwks, token)
    payload = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        issuer=settings.expected_issuer,
        options={"verify_aud": False},
    )
    return _extract_claims(payload)


def invalidate_jwks_cache() -> None:
    """Force a refresh on the next call to ``validate_token``. Useful in tests."""
    global _jwks_fetched_at
    _jwks_fetched_at = 0.0


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)

# Permission required to invoke the sign-xml endpoint (RF-FIR-11, D-01).
_PERM_FIRMA = "PERM_FIRMA"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    x_tenant_slug: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    FastAPI dependency — validates Bearer JWT, checks PERM_FIRMA, and binds tenant.

    Raises 401 if no valid token is present.
    Raises 403 if:
      - the token is valid but the subject lacks PERM_FIRMA in permissions[] (D-01), or
      - the token's tenant_slug does not match the X-Tenant-Slug request header.
    Returns the normalised claims dict on success.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "Bearer token required",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = await validate_token(credentials.credentials)
    except Exception as exc:
        logger.debug("JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "Invalid or expired token",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Check PERM_FIRMA against permissions[], not roles[] (ADR-013: D-01).
    if _PERM_FIRMA not in claims.get("permissions", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": f"{_PERM_FIRMA} permission is required to sign documents",
                "status": 403,
            },
        )

    # Tenant binding (fail-closed): a token issued for tenant A must not operate
    # on tenant B, and a token without a tenant_slug claim must not act on any
    # tenant schema. This service validates the JWT itself, so it enforces the
    # binding strictly instead of tolerating a missing claim/header.
    token_tenant = claims.get("tenant_slug")
    if not token_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": "Token carries no tenant_slug claim; cannot sign on any tenant",
                "status": 403,
            },
        )
    if x_tenant_slug and token_tenant != x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": (
                    f"Token tenant '{token_tenant}' does not match "
                    f"request tenant '{x_tenant_slug}'"
                ),
                "status": 403,
            },
        )

    return claims
