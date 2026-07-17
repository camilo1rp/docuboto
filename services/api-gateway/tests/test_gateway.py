# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from unittest.mock import AsyncMock

import httpx
import pytest

from tests.conftest import make_validate_response

pytestmark = pytest.mark.asyncio


async def test_proxy_auth_token_public_route(test_client):
    """POST /api/v1/auth/token is public — no JWT required, proxied to auth-service."""
    client, http_client, _ = test_client

    upstream_response = httpx.Response(
        status_code=200,
        json={
            "access_token": "tok",
            "refresh_token": "ref",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
        headers={"content-type": "application/json"},
    )
    http_client.request = AsyncMock(return_value=upstream_response)

    response = await client.post(
        "/api/v1/auth/token",
        json={"username": "u", "password": "p", "tenant_slug": "icetex"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "tok"
    http_client.request.assert_called_once()
    call_kwargs = http_client.request.call_args
    assert "auth-service:8001" in call_kwargs.kwargs["url"]


async def test_proxy_requires_auth(test_client):
    """GET /api/v1/tenants/ without Authorization header returns 401."""
    client, _, _ = test_client

    response = await client.get("/api/v1/tenants/")

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthorized"
    assert body["status"] == 401


async def test_proxy_invalid_token(test_client):
    """Auth-service returns valid:false — gateway returns 401."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=False))

    response = await client.get(
        "/api/v1/tenants/",
        headers={"Authorization": "Bearer bad-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


async def test_proxy_valid_token_forwards_request(test_client):
    """Valid token causes proxy to forward to tenant-service and return its response."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))

    upstream_body = {"items": [{"id": "abc", "slug": "icetex"}], "total": 1}
    http_client.request = AsyncMock(
        return_value=httpx.Response(
            status_code=200,
            json=upstream_body,
            headers={"content-type": "application/json"},
        )
    )

    response = await client.get(
        "/api/v1/tenants/",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.json() == upstream_body

    # Verify auth headers were injected into the upstream request
    request_call = http_client.request.call_args
    forwarded_headers = request_call.kwargs["headers"]
    assert "X-User-Id" in forwarded_headers
    assert "X-Tenant-Slug" in forwarded_headers
    assert forwarded_headers["X-Tenant-Slug"] == "icetex"


async def test_proxy_service_unavailable(test_client):
    """When upstream service is unreachable, gateway returns 503."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    http_client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    response = await client.get(
        "/api/v1/documents/",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "upstream_unavailable"


async def test_rate_limit_exceeded(test_client):
    """When IP count exceeds per-IP limit, gateway returns 429 with Retry-After."""
    client, http_client, mock_redis = test_client

    # Simulate counter above per-IP limit (101 > 100).
    # Also prepare a valid-token response in case JWT middleware runs before rate
    # limiting — the 429 must be asserted regardless of middleware evaluation order.
    pipeline = mock_redis.pipeline.return_value
    pipeline.execute = AsyncMock(return_value=[101, True, 5, True])
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))

    response = await client.get(
        "/api/v1/tenants/",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limit_exceeded"
    assert response.headers.get("retry-after") == "60"


async def test_health_check(test_client):
    """GET /health agrega estado de Redis y todos los servicios downstream."""
    client, http_client, _ = test_client

    # Todos los servicios downstream responden OK
    http_client.get = AsyncMock(return_value=httpx.Response(200, json={"status": "ok"}))

    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["auth-service"] == "ok"
    assert body["checks"]["tenant-service"] == "ok"
    assert body["checks"]["document-service"] == "ok"


async def test_health_check_degraded(test_client):
    """GET /health devuelve 503 si un servicio downstream falla."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert "unreachable" in body["checks"]["auth-service"]


async def test_request_id_header(test_client):
    """Every response includes X-Request-Id and X-Gateway-Version headers."""
    client, _, _ = test_client

    response = await client.get("/health")

    assert "x-request-id" in response.headers
    assert response.headers["x-gateway-version"] == "1.0.0"
    # UUID format check — 36 chars with dashes
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 36
    assert request_id.count("-") == 4


async def test_auth_service_unavailable(test_client):
    """When auth-service is unreachable during token validation, return 503."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(side_effect=httpx.ConnectError("auth-service not reachable"))

    response = await client.get(
        "/api/v1/tenants/",
        headers={"Authorization": "Bearer some-token"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "auth_service_unavailable"


async def test_openapi_spec_aggregation(test_client):
    """GET /api/v1/openapi.json returns aggregated OpenAPI spec from all services."""
    client, http_client, _ = test_client

    # Mock each service's /openapi.json endpoint
    def mock_get(url: str, **kwargs):
        """Return service-specific OpenAPI specs."""
        if "auth-service" in url:
            return httpx.Response(
                200,
                json={
                    "paths": {
                        "/token": {
                            "post": {
                                "summary": "Get access token",
                                "tags": ["auth"],
                            }
                        }
                    },
                },
            )
        elif "tenant-service" in url:
            return httpx.Response(
                200,
                json={
                    "paths": {
                        "/": {
                            "get": {
                                "summary": "List tenants",
                                "tags": ["tenants"],
                            }
                        }
                    },
                },
            )
        return httpx.Response(200, json={"paths": {}})

    http_client.get = AsyncMock(side_effect=mock_get)

    response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    spec = response.json()

    # Verify aggregation
    assert spec["openapi"] == "3.1.0"
    assert "OrpycaMCP" in spec["info"]["title"]
    assert "/api/v1/auth/token" in spec["paths"]
    assert "/api/v1/tenants/" in spec["paths"]
    assert "/health" in spec["paths"]

    # Verify security scheme is included
    assert "bearerAuth" in spec["components"]["securitySchemes"]


async def test_route_to_upstream_domain_prefixes():
    """Los prefijos de dominio se enrutan a su servicio (consolidación F1)."""
    from types import SimpleNamespace

    from app.routers.proxy import _route_to_upstream

    state = SimpleNamespace(
        auth_service_url="http://auth:8001",
        tenant_service_url="http://tenant:8002",
        document_service_url="http://document:8003",
        archive_service_url="http://archive:8004",
        storage_service_url="http://storage:8005",
        workflow_service_url="http://workflow:8006",
        notification_service_url="http://notification:8007",
        signature_service_url="http://signature:8008",
        mcp_service_url="http://mcp:8009",
        knowledge_service_url="http://knowledge:8011",
        assistant_proxy_timeout=180.0,
    )
    cases = {
        "/api/v1/audit": "http://auth:8001",
        "/api/v1/dependencias/by-codigo/120": "http://tenant:8002",
        "/api/v1/business-days/calculate": "http://tenant:8002",
        "/api/v1/config/params": "http://tenant:8002",
        "/api/v1/catalogos/tipos-anexo": "http://tenant:8002",
        "/api/v1/metadata/templates": "http://document:8003",
        "/api/v1/search": "http://document:8003",
        "/api/v1/expedientes/abc": "http://archive:8004",
        "/api/v1/tipos-documentales": "http://archive:8004",
        "/api/v1/trd/series": "http://archive:8004",
        "/api/v1/workflows/inbox": "http://workflow:8006",
        "/api/v1/workflow/rules/eval": "http://workflow:8006",
        # E18: el asistente conversacional enruta al mcp-server.
        "/api/v1/assistant/message": "http://mcp:8009",
        # E21: solo /search del knowledge-service se expone vía gateway.
        "/api/v1/knowledge/search": "http://knowledge:8011",
    }
    for path, expected in cases.items():
        assert _route_to_upstream(path, state) == expected, path

    assert _route_to_upstream("/api/v1/unknown", state) is None
    # El catálogo/protocolo MCP NO se expone por el gateway (solo /assistant/).
    assert _route_to_upstream("/api/v1/mcp/tools", state) is None
    # /ingest es interno (X-Internal-Token) y NUNCA debe enrutarse desde el exterior.
    assert _route_to_upstream("/api/v1/knowledge/ingest", state) is None
    assert _route_to_upstream("/api/v1/knowledge", state) is None


async def test_public_verify_route_is_public_and_routed():
    """E13: /api/v1/public/ es público (sin JWT) y enruta a document-service."""
    from types import SimpleNamespace

    from app.middleware.auth import _is_public
    from app.routers.proxy import _route_to_upstream

    assert _is_public("/api/v1/public/demo/verify/abc") is True
    state = SimpleNamespace(
        auth_service_url="http://auth:8001",
        tenant_service_url="http://tenant:8002",
        document_service_url="http://document:8003",
        archive_service_url="http://archive:8004",
        storage_service_url="http://storage:8005",
        workflow_service_url="http://workflow:8006",
        notification_service_url="http://notification:8007",
        signature_service_url="http://signature:8008",
        mcp_service_url="http://mcp:8009",
        knowledge_service_url="http://knowledge:8011",
        assistant_proxy_timeout=180.0,
    )
    assert _route_to_upstream("/api/v1/public/demo/verify/abc", state) == "http://document:8003"


# ---------------------------------------------------------------------------
# D-03: canal derivation and X-Canal header security
# ---------------------------------------------------------------------------

async def test_derive_canal_returns_api_for_standard_path():
    """D-03: caminos normales reciben canal 'api'."""
    from app.middleware.auth import derive_canal

    assert derive_canal("/api/v1/documents/") == "api"
    assert derive_canal("/api/v1/tenants/") == "api"
    assert derive_canal("/api/v1/auth/validate") == "api"
    assert derive_canal("/api/v1/workflows/inbox") == "api"


async def test_derive_canal_returns_mcp_for_mcp_prefix():
    """D-03: el espacio de nombres /api/v1/mcp/ recibe canal 'mcp'."""
    from app.middleware.auth import derive_canal

    assert derive_canal("/api/v1/mcp/query") == "mcp"
    assert derive_canal("/api/v1/mcp/") == "mcp"


async def test_client_x_canal_header_is_not_forwarded(test_client):
    """D-03: X-Canal enviado por el cliente NO debe llegar al upstream."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    http_client.request = AsyncMock(
        return_value=httpx.Response(
            status_code=200,
            json={"ok": True},
            headers={"content-type": "application/json"},
        )
    )

    await client.get(
        "/api/v1/documents/",
        headers={"Authorization": "Bearer valid-token", "X-Canal": "mcp"},
    )

    forwarded = http_client.request.call_args.kwargs["headers"]
    # Client-supplied X-Canal must not reach the upstream.
    assert forwarded.get("X-Canal") != "mcp", (
        "Client-supplied X-Canal must be discarded at the gateway"
    )


async def test_gateway_injects_server_derived_x_canal(test_client):
    """D-03: el gateway inyecta X-Canal derivado server-side, no el del cliente."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    http_client.request = AsyncMock(
        return_value=httpx.Response(
            status_code=200,
            json={"ok": True},
            headers={"content-type": "application/json"},
        )
    )

    # Client sends no X-Canal; gateway must inject 'api' for a standard path.
    await client.get(
        "/api/v1/documents/",
        headers={"Authorization": "Bearer valid-token"},
    )

    forwarded = http_client.request.call_args.kwargs["headers"]
    assert forwarded.get("X-Canal") == "api", (
        "Gateway must inject server-derived X-Canal='api' for standard API paths"
    )
