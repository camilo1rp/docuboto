# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.core.security import get_current_user
from app.main import app

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
USER = uuid.UUID("11111111-1111-1111-1111-111111111111")

# Claims returned by the mocked get_current_user.
# PERM_FIRMA lives in permissions[] (fine-grained ACL, ADR-013), not in roles[].
VALID_CLAIMS: dict = {
    "user_id": str(USER),
    "username": "testuser",
    "email": "test@demo.example",
    "tenant_slug": "demo",
    "roles": [],
    "permissions": ["PERM_FIRMA"],
    "exp": None,
    "iat": None,
}


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None  # used by audit.append (prev_hash lookup)
    # audit.append uses `async with conn.transaction():`
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


@pytest_asyncio.fixture
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) with PERM_FIRMA claims and tenant conn overridden."""
    conn = _make_mock_conn()

    async def _conn_dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    async def _auth_dep() -> dict:
        return VALID_CLAIMS

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    app.dependency_overrides[get_current_user] = _auth_dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.clear()
