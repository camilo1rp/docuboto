# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for tenant-service.

The DB dependency (get_db_conn) is overridden with an AsyncMock connection
so tests run without a real PostgreSQL instance.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn
from app.main import app


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # SELECT 1 → health check passes
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    return conn


def make_tenant_record(
    *,
    slug: str = "demo",
    name: str = "Demo Institution",
    code: str = "DEMO",
    status: str = "active",
    tenant_id: uuid.UUID | None = None,
) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": tenant_id or uuid.uuid4(),
        "slug": slug,
        "name": name,
        "code": code,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) for tests that need to configure mock_conn behaviour."""
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_conn

    app.dependency_overrides.clear()
