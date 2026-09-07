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
        app.state.mcp_service_url = settings.mcp_service_url
        app.state.knowledge_service_url = settings.knowledge_service_url
        app.state.assistant_proxy_timeout = settings.assistant_proxy_timeout
        app.state.rate_limit_per_ip = 100
        app.state.rate_limit_global = 1000
        app.state.max_request_body_bytes = settings.max_request_body_bytes
        app.state.max_public_body_bytes = settings.max_public_body_bytes
        app.state.max_upload_body_bytes = settings.max_upload_body_bytes
        # Las pruebas que ejercitan la documentación lo encienden; el resto hereda
        # el valor de producción, que es cerrado.
        app.state.openapi_publico = True
        app.state.trusted_proxies = settings.trusted_proxies
        app.state.rate_limit_pqrs_per_ip = settings.rate_limit_pqrs_per_ip
        app.state.rate_limit_cosecha_per_ip = settings.rate_limit_cosecha_per_ip
        app.state.auth_cache_ttl = settings.auth_cache_ttl
        yield client, mock_http_client, mock_redis


def make_validate_response(
    valid: bool = True,
    user_id: str = "user-123",
    username: str = "testuser",
    tenant_slug: str = "icetex",
    roles: list[str] | None = None,
) -> httpx.Response:
    return _make_validate_response(valid, user_id, username, tenant_slug, roles)


def wire_upstream(
    http_client: AsyncMock,
    response: httpx.Response | None = None,
    *,
    error: Exception | None = None,
    raw: bytes | None = None,
) -> None:
    """Cablea el doble del cliente HTTP para el proxy en STREAMING.

    El gateway ya no hace `http_client.request(...)`: construye la petición con
    `build_request` (síncrono) y la envía con `send(..., stream=True)`, para que ni
    el cuerpo de subida ni el de bajada se materialicen en memoria. Los dobles
    tienen que reflejar esa forma o los tests estarían comprobando una API que el
    código ya no usa.

    Las aserciones sobre cabeceras/timeout siguen mirando
    `http_client.build_request.call_args`, que es donde viajan ahora.
    """
    http_client.build_request = MagicMock(side_effect=lambda **kwargs: kwargs)

    if error is not None:
        http_client.send = AsyncMock(side_effect=error)
        return

    assert response is not None

    stream_stub = MagicMock()
    stream_stub.status_code = response.status_code
    stream_stub.headers = response.headers

    # `response.content` DECODIFICA si la respuesta trae `content-encoding`; el
    # gateway reenvía crudo, así que el doble tiene que poder devolver los bytes
    # sin decodificar (`raw`) para los tests que ejercitan esa cabecera.
    cuerpo_crudo = raw if raw is not None else response.content

    async def _aiter_raw():
        yield cuerpo_crudo

    stream_stub.aiter_raw = _aiter_raw
    stream_stub.aclose = AsyncMock()

    async def _send(peticion, stream=False):
        # El httpx real CONSUME el cuerpo al escribirlo en el socket. Si el doble
        # no lo consume, el guard de tamaño (`_limited_stream`, que solo puede
        # dispararse al iterar) nunca se ejecuta y un test del tope pasaría en
        # verde contra un gateway sin tope. El doble tiene que ser fiel en esto.
        cuerpo = peticion.get("content") if isinstance(peticion, dict) else None
        if cuerpo is not None and hasattr(cuerpo, "__aiter__"):
            async for _ in cuerpo:
                pass
        return stream_stub

    http_client.send = AsyncMock(side_effect=_send)
    http_client.send.return_value = stream_stub
