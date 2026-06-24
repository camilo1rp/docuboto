# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Fixtures de notification-service.

La BD (pool + get_tenant_conn + acquire_tenant) y Redis se mockean para correr
sin infraestructura. El historial se persiste/lee en la BD del tenant (E16).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def make_redis_mock() -> MagicMock:
    mock = MagicMock()
    mock.ping = AsyncMock()
    mock.xgroup_create = AsyncMock()
    mock.xreadgroup = AsyncMock(return_value=None)
    mock.xack = AsyncMock()
    mock.aclose = AsyncMock()
    return mock


def make_db_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []
    return conn


class _FakeAcquire:
    """Sustituye a acquire_tenant(slug): async context que cede el mock conn."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


@pytest.fixture
def redis_mock() -> MagicMock:
    return make_redis_mock()


@pytest.fixture
def db_conn() -> AsyncMock:
    return make_db_conn()


@pytest.fixture
def app_with_mocks(redis_mock: MagicMock, db_conn: AsyncMock):
    from app.core.database import get_tenant_conn

    async def _tenant_conn() -> AsyncGenerator[AsyncMock, None]:
        yield db_conn

    with (
        patch("app.main.init_pool", new=AsyncMock()),
        patch("app.main.close_pool", new=AsyncMock()),
        patch("app.main.init_redis", new=AsyncMock(return_value=redis_mock)),
        patch("app.main.close_redis", new=AsyncMock()),
        patch(
            "app.main.stream_worker.run",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch("app.core.redis_client.get_redis", return_value=redis_mock),
        patch("app.routers.health.get_redis", return_value=redis_mock),
        patch(
            "app.services.notification_service.acquire_tenant",
            return_value=_FakeAcquire(db_conn),
        ),
    ):
        from app.main import app

        app.dependency_overrides[get_tenant_conn] = _tenant_conn
        yield app
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app_with_mocks) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app_with_mocks), base_url="http://test"
    ) as ac:
        yield ac


def make_db_row(
    *,
    nid: uuid.UUID | None = None,
    recipient_email: str = "user@example.com",
    subject: str = "Subject",
    body: str = "Body",
    status: str = "sent",
    event_type: str | None = None,
    sent_at: datetime | None = None,
    attempts: int = 1,
    last_error: str | None = None,
) -> dict:
    return {
        "id": nid or uuid.uuid4(),
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "status": status,
        "event_type": event_type,
        "sent_at": sent_at or datetime.now(tz=UTC),
        "attempts": attempts,
        "last_error": last_error,
    }


TENANT_HEADERS = {"X-Tenant-Slug": "icetex"}
