# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.core.proxy import proxy_request

router = APIRouter()


def _route_to_upstream(path: str, app_state: Any) -> str | None:
    """Map a request path to its upstream service base URL."""
    # Cada servicio sirve bajo prefijos de DOMINIO (no solo /<service>/): se mapean
    # todos para que sus endpoints sean alcanzables vía gateway.
    routing: list[tuple[str, str]] = [
        # auth-service
        ("/api/v1/auth/", app_state.auth_service_url),
        ("/api/v1/audit/", app_state.auth_service_url),
        # tenant-service
        # /api/v1/tenants/ NO se enruta a propósito (hallazgo Crítico de
        # seguridad, auditoría 2026-07): el registro de tenants vive en el
        # schema `public` (get_db_conn, sin search_path de tenant) — cualquier
        # usuario autenticado de la entidad A podía enumerar y ESCRIBIR el
        # registro de la entidad B. No hay consumidor legítimo desde el
        # exterior: el frontend no lo llama (verificado, ningún fetch en
        # frontend/src) y el alta de tenants la hace `scripts/init_tenant.py`
        # escribiendo directo a `public.tenants` (operación de plataforma, no
        # de aplicación). El agregador de OpenAPI del gateway
        # (`routers/openapi.py`) sigue llamando a tenant-service directo por
        # su URL interna para /docs — eso no pasa por esta tabla de ruteo.
        ("/api/v1/dependencias/", app_state.tenant_service_url),
        ("/api/v1/catalogos/", app_state.tenant_service_url),
        ("/api/v1/config/", app_state.tenant_service_url),
        ("/api/v1/business-days/", app_state.tenant_service_url),
        ("/api/v1/pinar/", app_state.tenant_service_url),
        # document-service
        ("/api/v1/export", app_state.document_service_url),
        ("/api/v1/import", app_state.document_service_url),
        ("/api/v1/documents/", app_state.document_service_url),
        ("/api/v1/metadata/", app_state.document_service_url),
        ("/api/v1/search/", app_state.document_service_url),
        ("/api/v1/reports/", app_state.document_service_url),
        # OAI-PMH de expedientes (archive-service, E11 RF-INT-06) DEBE ir ANTES
        # que el prefijo genérico "/api/v1/public/" de document-service (más
        # abajo): el ruteo es primer-prefijo-que-casa, así que si quedara
        # después lo capturaría siempre document-service.
        ("/api/v1/public/archive/", app_state.archive_service_url),
        ("/api/v1/public/", app_state.document_service_url),
        ("/api/v1/ingest/", app_state.document_service_url),
        ("/api/v1/envios/", app_state.document_service_url),
        ("/api/v1/plantillas/", app_state.document_service_url),
        ("/api/v1/borradores/", app_state.document_service_url),
        # Batch API (carga masiva): document-service y archive-service comparten
        # el mismo prefijo raíz "/api/v1/batch" pero cada uno posee un tipo de
        # trabajo disjunto (documents vs expedientes) tanto en el POST de alta
        # como en el GET de estado -- el contrato anida el estado bajo el tipo
        # (`/batch/documents/{job_id}/status`, `/batch/expedientes/{job_id}/status`)
        # precisamente para que el ruteo por-prefijo sea determinista: antes,
        # ambos servicios definían `GET /api/v1/batch/{job_id}/status` con la
        # misma forma y ningún prefijo podía distinguirlos.
        ("/api/v1/batch/documents", app_state.document_service_url),
        ("/api/v1/batch/expedientes", app_state.archive_service_url),
        # archive-service
        ("/api/v1/archive/", app_state.archive_service_url),
        ("/api/v1/expedientes/", app_state.archive_service_url),
        ("/api/v1/expediente-metadata/", app_state.archive_service_url),
        ("/api/v1/tipos-documentales/", app_state.archive_service_url),
        ("/api/v1/trd/", app_state.archive_service_url),
        # ADR-026 (Increment 1): TVD es el mismo instrumento que TRD
        # (discriminador `tipo_instrumento` sobre `trd_series`) -- ruta
        # separada, sin ella el frontend recibe 404 sin pista del gateway.
        ("/api/v1/tvd/", app_state.archive_service_url),
        ("/api/v1/ubicaciones/", app_state.archive_service_url),
        ("/api/v1/unidades/", app_state.archive_service_url),
        ("/api/v1/prestamos/", app_state.archive_service_url),
        ("/api/v1/fuid", app_state.archive_service_url),
        ("/api/v1/transferencias/", app_state.archive_service_url),
        # storage-service — SOLO /upload se expone (el frontend sube directo).
        # D-02 (remediación no-read-up en anexos, RF-SEG-08): los endpoints
        # que sirven bytes/URL/metadata de un archivo (download presigned,
        # content, metadata) quedan protegidos por X-Internal-Token en
        # storage-service y NO se enrutan desde el exterior — la descarga
        # pasa SIEMPRE por el mediador de document-service
        # (`GET /api/v1/documents/{id}/anexos/{file_id}/download`).
        ("/api/v1/storage/upload", app_state.storage_service_url),
        ("/api/v1/preservacion/", app_state.storage_service_url),
        # workflow-service
        ("/api/v1/workflows/", app_state.workflow_service_url),
        # El motor de reglas usa prefijo singular `workflow/rules` (E05); enrutarlo.
        ("/api/v1/workflow/rules/", app_state.workflow_service_url),
        # Admin DLQ (ADR-021 Fase 5) usa prefijo singular `workflow/admin`; enrutarlo.
        ("/api/v1/workflow/admin/", app_state.workflow_service_url),
        # notification-service
        ("/api/v1/notifications/", app_state.notification_service_url),
        ("/api/v1/webhooks/", app_state.notification_service_url),
        # Admin DLQ (ADR-021 Fase 5 Inc.2) usa prefijo singular `notification/admin`; enrutarlo.
        ("/api/v1/notification/admin/", app_state.notification_service_url),
        # signature-service
        ("/api/v1/signature/", app_state.signature_service_url),
        # mcp-server — asistente conversacional (E18/ADR-019).
        ("/api/v1/assistant/", app_state.mcp_service_url),
        # Catálogo y protocolo MCP para clientes EXTERNOS. Antes no se enrutaba,
        # y eso dejaba el diseño a medias: la decisión era «un solo catálogo y un
        # solo gate para el asistente integrado y para clientes MCP externos», y
        # los externos no tenían por dónde entrar salvo hablando con el servicio
        # a espaldas del gateway — es decir, sin su validación JWT ni la
        # inyección de `X-Tenant-Slug`.
        #
        # Cubre `/api/v1/mcp/tools`, `/tools/{name}/invoke`,
        # `/proposals/execute` y `/api/v1/mcp/stream` (el transporte streamable,
        # montado también bajo este prefijo justo para no tener que abrirle al
        # proxy una forma de ruta fuera de `/api/v1/...`).
        #
        # Se puede exponer porque el gate de escritura ya no depende del cliente:
        # invocar una escritura por aquí devuelve una PROPUESTA firmada y no
        # ejecuta nada; ejecutarla exige el token firmado, atado a ese usuario y
        # a esa ventana. Con `X-Confirm-Write` —un booleano que cualquiera
        # fabricaba— enrutar esto habría sido abrir la escritura al exterior.
        ("/api/v1/mcp/", app_state.mcp_service_url),
        # knowledge-service (E21) — SOLO /search, /antecedentes (Inc.2, Patrón A —
        # recuperación anclada/citada, sin LLM) y /rag (Inc.3, Patrón B — RAG
        # generativo con citas) se exponen; /ingest es interno (X-Internal-Token)
        # y NO se enruta.
        ("/api/v1/knowledge/search", app_state.knowledge_service_url),
        ("/api/v1/knowledge/antecedentes", app_state.knowledge_service_url),
        ("/api/v1/knowledge/rag", app_state.knowledge_service_url),
    ]
    # Also match exact prefix without trailing slash
    for prefix, base_url in routing:
        if path.startswith(prefix) or path == prefix.rstrip("/"):
            return base_url
    return None


async def _handle_proxy(request: Request, path: str) -> Response:
    upstream_base = _route_to_upstream("/" + path, request.app.state)

    if upstream_base is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "route_not_found",
                "detail": f"Ninguna ruta del sistema corresponde a /{path}",
                "status": 404,
            },
        )

    upstream_url = f"{upstream_base.rstrip('/')}/{path}"

    extra_headers: dict[str, str] = {}
    auth_headers = getattr(request.state, "auth_headers", None)
    if auth_headers:
        extra_headers.update(auth_headers)

    http_client: httpx.AsyncClient = request.app.state.http_client

    # El asistente (E18) necesita un timeout mucho mayor que el global (loop LLM).
    timeout: float | None = None
    if upstream_base == request.app.state.mcp_service_url:
        timeout = request.app.state.assistant_proxy_timeout

    try:
        return await proxy_request(
            http_client=http_client,
            upstream_url=upstream_url,
            request=request,
            extra_headers=extra_headers,
            timeout=timeout,
            max_body_bytes=request.app.state.max_request_body_bytes,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=503,
            content={
                "error": "upstream_timeout",
                "detail": "El servicio no respondió a tiempo",
                "status": 503,
            },
        )
    except httpx.RequestError:
        return JSONResponse(
            status_code=503,
            content={
                "error": "upstream_unavailable",
                "detail": "No se pudo contactar el servicio",
                "status": 503,
            },
        )


@router.api_route(
    "/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
)
async def proxy(request: Request, path: str) -> Response:
    return await _handle_proxy(request, f"api/v1/{path}")
