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

from tests.conftest import make_validate_response, wire_upstream

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
    wire_upstream(http_client, upstream_response)

    response = await client.post(
        "/api/v1/auth/token",
        json={"username": "u", "password": "p", "tenant_slug": "icetex"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "tok"
    http_client.build_request.assert_called_once()
    call_kwargs = http_client.build_request.call_args
    assert "auth-service:8001" in call_kwargs.kwargs["url"]


async def test_proxy_requires_auth(test_client):
    """GET /api/v1/dependencias/ without Authorization header returns 401.

    (Antes usaba /api/v1/tenants/ como ejemplo genérico; ese prefijo ya NO se
    enruta desde el gateway — hallazgo Crítico de seguridad, auditoría
    2026-07: el registro de tenants vivía en `public`, sin scoping de tenant,
    y cualquier usuario autenticado de la entidad A podía leer/escribir el
    registro de la entidad B. /dependencias/ sigue siendo tenant-service.)
    """
    client, _, _ = test_client

    response = await client.get("/api/v1/dependencias/")

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthorized"
    assert body["status"] == 401


async def test_proxy_invalid_token(test_client):
    """Auth-service returns valid:false — gateway returns 401."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=False))

    response = await client.get(
        "/api/v1/dependencias/",
        headers={"Authorization": "Bearer bad-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


async def test_proxy_valid_token_forwards_request(test_client):
    """Valid token causes proxy to forward to tenant-service and return its response."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))

    upstream_body = {"items": [{"id": "abc", "codigo": 100}], "total": 1}
    wire_upstream(http_client, httpx.Response(
            status_code=200,
            json=upstream_body,
            headers={"content-type": "application/json"},
        )
    )

    response = await client.get(
        "/api/v1/dependencias/",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.json() == upstream_body

    # Verify auth headers were injected into the upstream request
    request_call = http_client.build_request.call_args
    forwarded_headers = request_call.kwargs["headers"]
    assert "X-User-Id" in forwarded_headers
    assert "X-Tenant-Slug" in forwarded_headers
    assert forwarded_headers["X-Tenant-Slug"] == "icetex"


async def test_tenants_endpoint_not_routed_even_with_valid_token(test_client):
    """Hallazgo 2 (Crítico, auditoría 2026-07): /api/v1/tenants/ no se enruta.

    Antes de esta remediación, un usuario autenticado de la entidad A podía
    GET/PATCH el registro de la entidad B (`routers/tenants.py` usa
    `get_db_conn`, sin `search_path` de tenant, y no tenía gate). La
    corrección más quirúrgica es sacar el prefijo de la tabla de ruteo: ya no
    hay upstream registrado, así que ni siquiera un token válido llega a
    tenant-service — el gateway responde 404 antes de proxyar nada.
    """
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(200, json={"leak": "should never be called"})
    )

    response = await client.get(
        "/api/v1/tenants/",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 404
    assert response.json()["error"] == "route_not_found"
    http_client.send.assert_not_called()

    response_patch = await client.patch(
        "/api/v1/tenants/00000000-0000-0000-0000-000000000000",
        json={"status": "suspended"},
        headers={"Authorization": "Bearer valid-token"},
    )
    assert response_patch.status_code == 404
    http_client.send.assert_not_called()


async def test_proxy_service_unavailable(test_client):
    """When upstream service is unreachable, gateway returns 503."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, error=httpx.ConnectError("Connection refused"))

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
        "/api/v1/dependencias/",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limit_exceeded"
    assert response.headers.get("retry-after") == "60"


async def test_health_es_vivacidad_pura(test_client):
    """`/health` gobierna los reinicios del contenedor: no puede depender de nadie.

    Antes consultaba a los diez servicios y devolvía 503 si alguno fallaba, así
    que notification caído hacía que el orquestador declarara enfermo al
    **gateway** — lo único capaz de seguir sirviendo el resto del sistema.
    """
    client, http_client, _ = test_client
    http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api-gateway"}
    assert http_client.get.await_count == 0, "no debe llamar a nadie"


async def test_ready_agrega_redis_y_los_diez_servicios(test_client):
    """La lista tenía SIETE entradas; el sistema tiene diez servicios detrás."""
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=httpx.Response(200, json={"status": "ok"}))

    response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["redis"] == "ok"
    for servicio in (
        "auth-service",
        "tenant-service",
        "document-service",
        "archive-service",
        "storage-service",
        "workflow-service",
        "notification-service",
        # Los tres que faltaban — y el diagnóstico se lee justo cuando algo va mal.
        "signature-service",
        "knowledge-service",
        "mcp-server",
    ):
        assert body["checks"][servicio] == "ok", servicio


async def test_ready_degradado_devuelve_503(test_client):
    client, http_client, _ = test_client
    http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["auth-service"] == "unreachable"


async def test_ready_no_publica_la_topologia_interna(test_client):
    """El texto de la excepción traía la URL interna del servicio.

    `f"unreachable: {exc}"` sobre un `ConnectError` de httpx incluye
    `http://document-service:8003/health`. La ruta no exige autenticación, así
    que el mapa de nombres y puertos internos quedaba a la vista de cualquiera
    que la consultara con un servicio caído.
    """
    client, http_client, _ = test_client
    http_client.get = AsyncMock(
        side_effect=httpx.ConnectError("[Errno -2] Name or service not known: document-service:8003")
    )

    cuerpo = (await client.get("/health/ready")).text

    assert "document-service:8003" not in cuerpo
    assert "Errno" not in cuerpo
    assert "8003" not in cuerpo


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


# --- 11.2: el identificador de petición tiene que LLEGAR a los servicios ---


async def test_request_id_viaja_al_upstream_y_coincide_con_la_respuesta(test_client):
    """El defecto: se generaba en la respuesta, después de haber proxyado.

    `x-request-id` estaba en `FORWARDED_REQUEST_HEADERS` desde siempre, así que
    la tubería existía y no pasaba nada por ella. Este test es el que la mide:
    el valor que ve el servicio y el que ve el cliente son EL MISMO.
    """
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response())
    wire_upstream(
        http_client,
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    )

    response = await client.get(
        "/api/v1/dependencias/",
        headers={"Authorization": "Bearer valid-token"},
    )

    reenviadas = {
        k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()
    }
    assert "x-request-id" in reenviadas
    assert reenviadas["x-request-id"] == response.headers["x-request-id"]


async def test_request_id_del_cliente_se_conserva(test_client):
    """El SSR del frontend continúa la petición del navegador: sin esto, la
    traza se parte justo en el salto más difícil de reconstruir."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response())
    wire_upstream(
        http_client,
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    )

    response = await client.get(
        "/api/v1/dependencias/",
        headers={"Authorization": "Bearer valid-token", "X-Request-Id": "ssr-abc-123"},
    )

    reenviadas = {
        k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()
    }
    assert reenviadas["x-request-id"] == "ssr-abc-123"
    assert response.headers["x-request-id"] == "ssr-abc-123"


async def test_request_id_malformado_del_cliente_se_descarta(test_client):
    """No es una credencial, pero sí acaba en registros: nada de saltos de línea
    ni de valores gigantes que se copien once veces."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response())
    wire_upstream(
        http_client,
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    )

    response = await client.get(
        "/api/v1/dependencias/",
        headers={
            "Authorization": "Bearer valid-token",
            # Espacio + carácter fuera del alfabeto permitido.
            "X-Request-Id": "no vale$",
        },
    )

    reenviadas = {
        k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()
    }
    assert reenviadas["x-request-id"] != "no vale$"
    assert len(reenviadas["x-request-id"]) == 36  # se generó uno nuevo
    assert response.headers["x-request-id"] == reenviadas["x-request-id"]


async def test_request_id_demasiado_largo_se_descarta(test_client):
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response())
    wire_upstream(
        http_client,
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    )

    await client.get(
        "/api/v1/dependencias/",
        headers={"Authorization": "Bearer valid-token", "X-Request-Id": "a" * 200},
    )

    reenviadas = {
        k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()
    }
    assert len(reenviadas["x-request-id"]) == 36


async def test_registro_de_acceso_lleva_tenant_usuario_y_no_la_cadena_de_consulta(
    test_client, caplog
):
    """El registro estructurado: quién, sobre qué ruta, con qué resultado.

    La cadena de consulta se queda fuera a propósito — lleva números de radicado
    y términos de búsqueda, y los registros del gateway no son sitio para el
    contenido de las consultas de nadie.
    """
    import json
    import logging

    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response())
    wire_upstream(
        http_client,
        httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"}),
    )

    with caplog.at_level(logging.INFO, logger="gateway.access"):
        await client.get(
            "/api/v1/dependencias/?q=2024-ICETEX-E-000001",
            headers={"Authorization": "Bearer valid-token"},
        )

    lineas = [json.loads(r.message) for r in caplog.records if r.name == "gateway.access"]
    assert lineas, "el gateway no registró la petición"
    registro = lineas[-1]
    assert registro["tenant"] == "icetex"
    assert registro["usuario"] == "user-123"
    assert registro["path"] == "/api/v1/dependencias/"
    assert registro["status"] == 200
    assert "2024-ICETEX-E-000001" not in json.dumps(registro)


# --- 11.7: CORS ---


async def test_cors_no_se_instala_sin_origenes_declarados():
    """`allow_origins=["*"]` + `allow_credentials=True` estaba en duro.

    Esa pareja no es permisiva: con credenciales Starlette no puede responder
    `*`, así que refleja el `Origin` recibido — cualquier página de internet
    podía llamar al gateway con las credenciales del visitante.

    Vacío por defecto no es una regresión: el frontend habla por SSR.
    """
    from app.core.config import Settings

    assert Settings().cors_allow_origins == ""
    assert Settings().cors_origins_list == []


async def test_cors_con_comodin_desactiva_las_credenciales():
    """Un API público de solo lectura es legítimo; comodín CON credenciales no
    lo es en ningún escenario."""
    from app.core.config import Settings

    s = Settings(cors_allow_origins="*")
    assert s.cors_origins_list == ["*"]

    s2 = Settings(cors_allow_origins="https://sgd.gov.co, https://portal.gov.co ,")
    assert s2.cors_origins_list == ["https://sgd.gov.co", "https://portal.gov.co"]


async def test_auth_service_unavailable(test_client):
    """When auth-service is unreachable during token validation, return 503."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(side_effect=httpx.ConnectError("auth-service not reachable"))

    response = await client.get(
        "/api/v1/dependencias/",
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
        # RF-RAD-05: el catálogo de destinatarios y la cascada DIVIPOLA. La
        # ruta EXACTA es la del listado y el alta; sin ella, dar de alta un
        # destinatario devolvía `route_not_found` y la cascada de `/radicar`
        # no tenía de dónde leer los municipios — construido y no ofrecido.
        "/api/v1/terceros": "http://document:8003",
        "/api/v1/terceros/divipola/departamentos": "http://document:8003",
        "/api/v1/terceros/divipola/municipios": "http://document:8003",
        "/api/v1/terceros/radicado/abc/actores": "http://document:8003",
        "/api/v1/search": "http://document:8003",
        "/api/v1/expedientes/abc": "http://archive:8004",
        # Batch API: mismo prefijo raíz "/api/v1/batch" en ambos servicios,
        # desambiguado por el tipo de trabajo en la subruta.
        "/api/v1/batch/documents": "http://document:8003",
        "/api/v1/batch/documents/abc/status": "http://document:8003",
        "/api/v1/batch/expedientes": "http://archive:8004",
        "/api/v1/batch/expedientes/abc/status": "http://archive:8004",
        "/api/v1/tipos-documentales": "http://archive:8004",
        "/api/v1/trd/series": "http://archive:8004",
        # ADR-026 (Increment 1): TVD es el mismo instrumento que TRD.
        "/api/v1/tvd/agrupaciones": "http://archive:8004",
        "/api/v1/workflows/inbox": "http://workflow:8006",
        "/api/v1/workflow/rules/eval": "http://workflow:8006",
        # E18: el asistente conversacional enruta al mcp-server.
        "/api/v1/assistant/message": "http://mcp:8009",
        # E18 (bloque 9 del plan MCP funcional): el catálogo y el protocolo MCP
        # SÍ se exponen ahora a clientes externos. Antes no, y eso dejaba a
        # medias la decisión de «un solo catálogo y un solo gate»: un cliente MCP
        # de terceros no tenía por dónde entrar salvo hablando con el servicio a
        # espaldas del gateway, sin validación JWT ni `X-Tenant-Slug`.
        "/api/v1/mcp/tools": "http://mcp:8009",
        "/api/v1/mcp/tools/radicar_documento/invoke": "http://mcp:8009",
        "/api/v1/mcp/proposals/execute": "http://mcp:8009",
        # El transporte streamable se monta TAMBIÉN bajo este prefijo (además de
        # `/mcp`) justo para que quepa por esta tabla, en vez de abrirle al proxy
        # una forma de ruta fuera de `/api/v1/...`.
        "/api/v1/mcp/stream": "http://mcp:8009",
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

    # Hallazgo Crítico (auditoría 2026-07): /api/v1/tenants/ NUNCA se enruta.
    # El registro de tenants vive en `public` sin scoping de tenant — antes de
    # este cambio, cualquier usuario autenticado de la entidad A podía
    # enumerar (GET) y ESCRIBIR (PATCH) el registro de la entidad B. No hay
    # consumidor legítimo externo (frontend no lo llama; el alta de tenants
    # es `scripts/init_tenant.py`, que escribe directo a la BD).
    assert _route_to_upstream("/api/v1/tenants", state) is None
    assert _route_to_upstream("/api/v1/tenants/", state) is None
    assert _route_to_upstream("/api/v1/tenants/by-slug/icetex", state) is None
    assert _route_to_upstream("/api/v1/unknown", state) is None
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
    wire_upstream(http_client, httpx.Response(
            status_code=200,
            json={"ok": True},
            headers={"content-type": "application/json"},
        )
    )

    await client.get(
        "/api/v1/documents/",
        headers={"Authorization": "Bearer valid-token", "X-Canal": "mcp"},
    )

    forwarded = http_client.build_request.call_args.kwargs["headers"]
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
    wire_upstream(http_client, httpx.Response(
            status_code=401,
            json={"detail": {"error": "missing_internal_token"}},
            headers={"content-type": "application/json"},
        )
    )

    await client.get(
        "/api/v1/preservacion/aip/EXP-2024-0001",
        headers={"Authorization": "Bearer valid-token", "X-Internal-Token": "forged"},
    )

    forwarded = http_client.build_request.call_args.kwargs["headers"]
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
    wire_upstream(http_client, httpx.Response(
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
    forwarded = http_client.build_request.call_args.kwargs["headers"]
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
    http_client.send = AsyncMock()  # no debería llamarse jamás

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
    http_client.send.assert_not_called()


async def test_x_internal_token_header_never_in_forwarded_request_headers():
    """Defensa en profundidad estructural (remediación #3): incluso si algún
    prefijo nuevo mapeara `/internal/...` a un upstream por error futuro,
    `X-Internal-Token` seguiría sin poder viajar -- no está en la whitelist
    `FORWARDED_REQUEST_HEADERS` (la fuente del invariante que
    `test_client_x_internal_token_header_is_not_forwarded*` ya prueba de
    forma indirecta, vía una petición HTTP real)."""
    from app.core.proxy import FORWARDED_REQUEST_HEADERS

    assert "x-internal-token" not in FORWARDED_REQUEST_HEADERS


async def test_postal_signature_header_in_forwarded_request_headers():
    """E20 F5: la firma HMAC del webhook postal DEBE estar en la whitelist —
    es la credencial de autenticación del operador que document-service verifica.
    Si se cae de la whitelist, todo callback legítimo fallaría 401 e2e (la puerta
    HMAC quedaría inalcanzable). Guarda de regresión del Hallazgo #1 de la auditoría."""
    from app.core.proxy import FORWARDED_REQUEST_HEADERS

    assert "x-orpycamcp-signature" in FORWARDED_REQUEST_HEADERS


async def test_postal_callback_forwards_signature_header(test_client):
    """E20 F5: el gateway reenvía `X-OrpycaMCP-Signature` al callback público
    (ruta `/api/v1/public/postal/callback/{slug}`, exenta de JWT) — sin esto el
    HMAC nunca llega a document-service y el callback siempre daría 401."""
    client, http_client, _ = test_client

    wire_upstream(http_client, httpx.Response(
            status_code=200,
            json={"status": "applied"},
            headers={"content-type": "application/json"},
        )
    )

    await client.post(
        "/api/v1/public/postal/callback/demo",
        headers={"X-OrpycaMCP-Signature": "sha256=deadbeef", "Content-Type": "application/json"},
        content=b'{"operador":"4-72"}',
    )

    # Starlette entrega las claves de header en minúscula; el gateway las
    # reenvía tal cual → la clave reenviada es 'x-orpycamcp-signature'.
    forwarded = {k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()}
    assert forwarded.get("x-orpycamcp-signature") == "sha256=deadbeef", (
        "Gateway must forward the operator's HMAC signature to document-service"
    )


async def test_gateway_injects_server_derived_x_canal(test_client):
    """D-03: el gateway inyecta X-Canal derivado server-side, no el del cliente."""
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(
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

    forwarded = http_client.build_request.call_args.kwargs["headers"]
    assert forwarded.get("X-Canal") == "api", (
        "Gateway must inject server-derived X-Canal='api' for standard API paths"
    )


@pytest.mark.asyncio
async def test_pagination_and_truncation_headers_are_forwarded_back(test_client):
    """`X-Total-Count` y `X-Truncated` deben llegar al cliente (test de contrato).

    No es una preferencia de estilo: son cabeceras de las que dependen contratos
    documentados del sistema, y su ausencia es SILENCIOSA en ambos casos —
    ninguna pantalla falla, simplemente miente.

    - `X-Total-Count` es la convención de paginación de `docs/es/api.md`; la
      emiten 31 routers y el frontend la consume en ~115 puntos. Si el gateway
      la descarta, el total colapsa al tamaño de la página y toda pantalla
      paginada cree que hay una sola.
    - `X-Truncated` es la garantía de honestidad de las tres DLQ (ADR-021): el
      backend avisa de que existen eventos pendientes más antiguos que no caben
      en la respuesta. Si se descarta, el banner que lo advierte nunca aparece y
      la pantalla afirma en silencio que se ve todo.

    Gemelo, del lado de respuesta, de los tests de FORWARDED_REQUEST_HEADERS.
    """
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(
            status_code=200,
            json=[{"id": "abc"}],
            headers={
                "content-type": "application/json",
                "X-Total-Count": "437",
                "X-Truncated": "true",
                "X-Faltantes": "TEL_R,DIR_P",
            },
        )
    )

    response = await client.get(
        "/api/v1/documents/",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.headers.get("x-total-count") == "437", (
        "El gateway debe reenviar X-Total-Count; sin ella la paginacion colapsa "
        "a una sola pagina en TODA pantalla paginada del sistema"
    )
    assert response.headers.get("x-truncated") == "true", (
        "El gateway debe reenviar X-Truncated; sin ella el aviso de truncamiento "
        "de las DLQ (ADR-021) es codigo muerto y la pantalla miente por omision"
    )
    assert response.headers.get("x-faltantes") == "TEL_R,DIR_P", (
        "El gateway debe reenviar X-Faltantes; el cuerpo de la previsualizacion "
        "es el PDF, asi que no hay otro sitio donde decir que campos quedaron "
        "sin valor (E23, RF-PRO-01) -- sin ella el oficio sale con un hueco y "
        "el fallo llega al ciudadano"
    )


async def test_proxy_sin_override_deja_el_timeout_del_cliente(test_client):
    """Sin override, `timeout` debe ser USE_CLIENT_DEFAULT — NUNCA None.

    Para httpx los dos valores son distintos y opuestos: USE_CLIENT_DEFAULT es
    "usa el que ya tiene el cliente"; None es "sin limite de tiempo". Pasar None
    dejaria cada peticion al upstream colgada indefinidamente y el 503
    `upstream_timeout` de este mismo router seria inalcanzable.
    """
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(
            status_code=200,
            json={"items": []},
            headers={"content-type": "application/json"},
        )
    )

    await client.get("/api/v1/documents/", headers={"Authorization": "Bearer valid-token"})

    timeout_arg = http_client.build_request.call_args.kwargs["timeout"]
    assert timeout_arg is httpx.USE_CLIENT_DEFAULT
    assert timeout_arg is not None


async def test_proxy_asistente_propaga_su_timeout_ampliado(test_client):
    """E18: la ruta del asistente propaga `assistant_proxy_timeout` al upstream.

    Su loop LLM excede el timeout global del cliente, asi que el override
    por-peticion es lo unico que evita cortar la respuesta a media generacion.
    """
    client, http_client, _ = test_client

    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(
            status_code=200,
            json={"reply": "ok"},
            headers={"content-type": "application/json"},
        )
    )

    await client.post(
        "/api/v1/assistant/message",
        json={"message": "hola"},
        headers={"Authorization": "Bearer valid-token"},
    )

    from app.core.config import settings

    assert http_client.build_request.call_args.kwargs["timeout"] == settings.assistant_proxy_timeout
    assert http_client.build_request.call_args.kwargs["timeout"] is not httpx.USE_CLIENT_DEFAULT
