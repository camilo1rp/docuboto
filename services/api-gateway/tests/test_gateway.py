# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
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
        # D-02 (remediación no-read-up en anexos, RF-SEG-08): solo /upload
        # del storage-service se expone (el frontend sube directo);
        # /preservacion/ sigue expuesto (fuera del alcance de esta remediación).
        "/api/v1/storage/upload": "http://storage:8005",
        "/api/v1/preservacion/proteger-indice": "http://storage:8005",
        # E10 Increment C: /preservacion/aip/{id} SIGUE enrutando por el mismo
        # prefijo blanket (el gateway no distingue sub-rutas) — el gate real
        # ahora vive en storage-service (X-Internal-Token, D-02): una llamada
        # externa a este path llega al upstream pero storage-service la
        # rechaza con 401 porque el gateway NUNCA reenvía X-Internal-Token
        # (no está en FORWARDED_REQUEST_HEADERS). El único acceso EXTERNO
        # legítimo al AIP pasa por el mediador de archive-service:
        # GET /api/v1/expedientes/{id}/aip (ver archive-service/app/routers/
        # expedientes.py::get_aip).
        "/api/v1/preservacion/aip/EXP-2024-0001": "http://storage:8005",
        # Continuación E10 Increment C (RF-SEG-08): mismo criterio que
        # /preservacion/aip/{id} — /preservacion/eventos SIGUE enrutando por
        # el prefijo blanket, pero storage-service ahora exige
        # X-Internal-Token (D-02) y el gateway nunca lo reenvía. El único
        # acceso EXTERNO legítimo es el mediador de archive-service:
        # GET /api/v1/expedientes/{id}/preservacion/eventos.
        "/api/v1/preservacion/eventos": "http://storage:8005",
    }
    for path, expected in cases.items():
        assert _route_to_upstream(path, state) == expected, path

    assert _route_to_upstream("/api/v1/unknown", state) is None
    # El catálogo/protocolo MCP NO se expone por el gateway (solo /assistant/).
    assert _route_to_upstream("/api/v1/mcp/tools", state) is None
    # /ingest es interno (X-Internal-Token) y NUNCA debe enrutarse desde el exterior.
    assert _route_to_upstream("/api/v1/knowledge/ingest", state) is None
    assert _route_to_upstream("/api/v1/knowledge", state) is None
    # D-02: la descarga directa de storage-service YA NO se enruta — pasa
    # SIEMPRE por el mediador de document-service (RF-SEG-08). Ídem metadata
    # y el stream de bytes interno (`/content`), ambos protegidos por
    # X-Internal-Token en storage-service.
    assert _route_to_upstream("/api/v1/storage/files/abc/download", state) is None
    assert _route_to_upstream("/api/v1/storage/files/abc/content", state) is None
    assert _route_to_upstream("/api/v1/storage/files/abc", state) is None
    # /internal/* está estructuralmente fuera de /api/v1 — ningún catch-all
    # de _route_to_upstream puede alcanzarlo por construcción (E17/F4,
    # Incremento 1 de firma personal PKI; mismo criterio ya válido para
    # /internal/totp/* desde el incremento TOTP).
    assert _route_to_upstream("/internal/signing-key/sign-digest", state) is None
    assert _route_to_upstream("/internal/signing-key/activate", state) is None
    assert _route_to_upstream("/internal/totp/verify", state) is None
    # Remediación #3 (auditoría cableado AIP E10-B): `POST
    # /api/v1/internal/documentos/anexos` (D-02, document-service, primer
    # endpoint interno de ese servicio) NO tiene ningún prefijo mapeado --
    # a diferencia de `/internal/*` de arriba (fuera de `/api/v1` por
    # construcción), este SÍ vive bajo `/api/v1/`, así que hace falta
    # confirmarlo explícitamente: ningún prefijo de la tabla (`/api/v1/documents/`
    # incluido) lo casa, porque el segmento es `internal`, no `documents`.
    assert _route_to_upstream("/api/v1/internal/documentos/anexos", state) is None


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


async def test_client_x_internal_token_header_is_not_forwarded(test_client):
    """E10 Increment C: un cliente externo NO puede forjar X-Internal-Token
    para alcanzar `GET /preservacion/aip/{id}` directamente en storage-service
    (D-02) — el gateway enruta el prefijo blanket `/api/v1/preservacion/`
    (sin distinguir sub-rutas), pero `X-Internal-Token` no está en
    `FORWARDED_REQUEST_HEADERS`, así que aunque un cliente lo envíe, storage-
    service nunca lo recibe y responde 401 fail-closed. El único acceso
    EXTERNO legítimo al AIP es el mediador `GET /api/v1/expedientes/{id}/aip`
    de archive-service."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    http_client.request = AsyncMock(
        return_value=httpx.Response(
            status_code=401,
            json={"detail": {"error": "missing_internal_token"}},
            headers={"content-type": "application/json"},
        )
    )

    await client.get(
        "/api/v1/preservacion/aip/EXP-2024-0001",
        headers={"Authorization": "Bearer valid-token", "X-Internal-Token": "forged"},
    )

    forwarded = http_client.request.call_args.kwargs["headers"]
    assert "X-Internal-Token" not in forwarded, (
        "Client-supplied X-Internal-Token must never reach an upstream service"
    )


async def test_client_x_internal_token_header_is_not_forwarded_for_eventos(test_client):
    """Continuación E10 Increment C (RF-SEG-08): mismo caso que el AIP, pero
    para `GET /preservacion/eventos` — un cliente externo NO puede forjar
    X-Internal-Token para alcanzarlo directamente en storage-service (D-02).
    El único acceso EXTERNO legítimo a los eventos de UN expediente es el
    mediador `GET /api/v1/expedientes/{id}/preservacion/eventos` de
    archive-service."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    http_client.request = AsyncMock(
        return_value=httpx.Response(
            status_code=401,
            json={"detail": {"error": "missing_internal_token"}},
            headers={"content-type": "application/json"},
        )
    )

    response = await client.get(
        "/api/v1/preservacion/eventos?object_ref=EXP-2024-0001",
        headers={"Authorization": "Bearer valid-token", "X-Internal-Token": "forged"},
    )

    assert response.status_code == 401
    forwarded = http_client.request.call_args.kwargs["headers"]
    assert "X-Internal-Token" not in forwarded, (
        "Client-supplied X-Internal-Token must never reach an upstream service"
    )


async def test_internal_anexos_path_is_not_routed_externally(test_client):
    """Remediación #3 (auditoría cableado AIP E10-B): `POST
    /api/v1/internal/documentos/anexos` (D-02, document-service) no tiene
    ningún prefijo mapeado en `_route_to_upstream` -- un cliente externo que
    intente alcanzarlo vía el gateway recibe 404 `route_not_found` ANTES de
    que la petición llegue siquiera a document-service (que además la
    rechazaría con `X-Internal-Token`, nunca reenviado -- ver el test de
    abajo). Mismo criterio que la inalcanzabilidad ya probada para
    `/internal/signing-key/*`/`/internal/totp/*`."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    http_client.request = AsyncMock()  # no debería llamarse jamás

    response = await client.post(
        "/api/v1/internal/documentos/anexos",
        json={"radicado_ids": [str(uuid.uuid4())]},
        headers={
            "Authorization": "Bearer valid-token",
            "X-Internal-Token": "forged",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"] == "route_not_found"
    http_client.request.assert_not_called()


async def test_x_internal_token_header_never_in_forwarded_request_headers():
    """Defensa en profundidad estructural (remediación #3): incluso si algún
    prefijo nuevo mapeara `/internal/...` a un upstream por error futuro,
    `X-Internal-Token` seguiría sin poder viajar -- no está en la whitelist
    `FORWARDED_REQUEST_HEADERS` (la fuente del invariante que
    `test_client_x_internal_token_header_is_not_forwarded*` ya prueba de
    forma indirecta, vía una petición HTTP real)."""
    from app.core.proxy import FORWARDED_REQUEST_HEADERS

    assert "x-internal-token" not in FORWARDED_REQUEST_HEADERS


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
