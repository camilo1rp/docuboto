# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for auth-service.

Keycloak is not required — all HTTP interactions are mocked.
The DB dependency (get_db_conn) is overridden with an AsyncMock connection
so tests run without a real PostgreSQL instance.
"""

import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_db_conn
from app.main import app
from app.routers.auth import set_auth_service
from app.services.auth_service import AuthService


class _FakeTx:
    """Context manager async no-op para simular conn.transaction()."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # SELECT 1 (health) / SELECT hash (audit) → no rompe
    conn.execute.return_value = None  # INSERT / advisory lock → no-op
    # orpycamcp_common.audit.append() envuelve el INSERT en `async with conn.transaction()`.
    conn.transaction = MagicMock(return_value=_FakeTx())
    return conn


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    service = AuthService()
    set_auth_service(service)
    app.dependency_overrides[get_db_conn] = override_get_db_conn

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await service.close()


def build_jwt_claims(
    *,
    username: str = "operador",
    tenant_slug: str | None = "demo",
    roles: list[str] | None = None,
    expired: bool = False,
) -> dict[str, Any]:
    now = int(time.time())
    return {
        "sub": str(uuid.uuid4()),
        "preferred_username": username,
        "email": f"{username}@orpycamcp.test",
        "tenant_slug": tenant_slug,
        "realm_access": {"roles": roles or ["operator"]},
        "iss": settings.expected_issuer,
        "iat": now - 60,
        "exp": (now - 10) if expired else (now + 300),
    }


FAKE_TOKEN_RESPONSE = {
    "access_token": "eyFAKE_ACCESS_TOKEN",
    "refresh_token": "eyFAKE_REFRESH_TOKEN",
    "token_type": "Bearer",
    "expires_in": 300,
}
