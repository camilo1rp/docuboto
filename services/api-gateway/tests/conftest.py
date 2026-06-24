# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


def _make_validate_response(
    valid: bool,
    user_id: str = "user-123",
    username: str = "testuser",
    tenant_slug: str = "icetex",
    roles: list[str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json={
            "valid": valid,
            "user_id": user_id,
            "username": username,
            "tenant_slug": tenant_slug,
            "roles": roles or ["viewer"],
        },
    )


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    pipeline = AsyncMock()
    pipeline.incr = MagicMock(return_value=pipeline)
    pipeline.expire = MagicMock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[1, True, 1, True])
    redis.pipeline = MagicMock(return_value=pipeline)
    redis.aclose = AsyncMock()
    return redis


@pytest.fixture
def mock_http_client():
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
async def test_client(mock_redis, mock_http_client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.state.http_client = mock_http_client
        app.state.redis = mock_redis
        app.state.auth_service_url = settings.auth_service_url
        app.state.tenant_service_url = settings.tenant_service_url
        app.state.document_service_url = settings.document_service_url
        app.state.archive_service_url = settings.archive_service_url
        app.state.storage_service_url = settings.storage_service_url
        app.state.workflow_service_url = settings.workflow_service_url
        app.state.notification_service_url = settings.notification_service_url
        app.state.signature_service_url = settings.signature_service_url
        app.state.rate_limit_per_ip = 100
        app.state.rate_limit_global = 1000
        yield client, mock_http_client, mock_redis


def make_validate_response(
    valid: bool = True,
    user_id: str = "user-123",
    username: str = "testuser",
    tenant_slug: str = "icetex",
    roles: list[str] | None = None,
) -> httpx.Response:
    return _make_validate_response(valid, user_id, username, tenant_slug, roles)
