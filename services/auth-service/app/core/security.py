# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
JWT validation against Keycloak's public JWKS endpoint.

The JWKS is cached in memory for `settings.jwks_cache_ttl` seconds to avoid
hitting Keycloak on every request.  A lock prevents stampedes when the cache
expires under concurrent load.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
from jose import JWTError, jwt

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
        # Double-check inside the lock — another coroutine may have refreshed.
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


def _extract_claims(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise raw JWT payload into the claims shape the platform uses."""
    realm_access = payload.get("realm_access", {})
    roles: list[str] = realm_access.get("roles", [])

    # tenant_slug is stored as a user attribute in Keycloak and mapped to the
    # token as a plain string claim via a protocol mapper.
    tenant_slug: str | None = payload.get("tenant_slug")

    return {
        "user_id": payload.get("sub", ""),
        "username": payload.get("preferred_username", ""),
        "email": payload.get("email", ""),
        "tenant_slug": tenant_slug,
        "roles": roles,
        "exp": payload.get("exp"),
        "iat": payload.get("iat"),
    }


async def validate_token(token: str) -> dict[str, Any]:
    """
    Validate a Bearer JWT against Keycloak's public JWKS.

    Returns normalised claims on success.
    Raises ``JWTError`` on any validation failure (expired, bad signature, etc.).
    """
    jwks = await _get_jwks()

    try:
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=None,  # Keycloak tokens may omit 'aud' for bearer-only flow
            options={"verify_aud": False},
        )
    except JWTError:
        raise

    return _extract_claims(payload)


def invalidate_jwks_cache() -> None:
    """Force a refresh on the next call to ``validate_token``.  Useful in tests."""
    global _jwks_fetched_at
    _jwks_fetched_at = 0.0
