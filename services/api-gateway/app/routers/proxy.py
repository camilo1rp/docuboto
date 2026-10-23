# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import posixpath
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.core.proxy import proxy_request

router = APIRouter()


def _normalize_path(path: str) -> str | None:
    """Ruta canónica para decidir Y para reenviar, o ``None`` si es sospechosa.

    Starlette decodifica la ruta una vez; ``httpx`` normaliza ``..`` al armar la
    petición. Si el gateway decidiera sobre la ruta cruda, ``x/../internal/...``
    pasaba el filtro y el upstream lo resolvía a la ruta interna. Aquí se decodifica
    hasta estabilizar (``%252e`` -> ``%2e`` -> ``.``), se rechaza todo segmento
    ``.``/``..``, barra doble, ``\\`` o carácter de control, y el resultado (en
    minúsculas solo para comparar; se reenvía la forma original) es lo que se enruta.
    """
    decoded = path
    for _ in range(8):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    else:
        return None  # decodificación anidada sin fin: ataque, no ruta
    if "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return None
    if not decoded.startswith("/"):
        return None
    segments = decoded.split("/")[1:]
    # El último segmento puede ir vacío (barra final legítima); los demás no.
    if any(seg == "" for seg in segments[:-1]) or any(seg in (".", "..") for seg in segments):
        return None
    if posixpath.normpath(decoded.rstrip("/") or "/") != (decoded.rstrip("/") or "/"):
        return None
    for seg in segments:
        low = seg.lower()
        # Actos de SISTEMA (X-Internal-Token, D-02) de cualquier servicio: document
        # `/internal/documentos`, archive `/internal/expedientes`, signature
        # `/internal/*`, storage `*-internal`. Nunca se enrutan desde el exterior.
        if low == "internal" or low.endswith("-internal"):
            return None
    return path


def _route_to_upstream(path: str, app_state: Any) -> str | None:
    """Map a request path to its upstream service base URL."""
    if _normalize_path(path) is None:
        return None
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
        # Cuentas de correo (E19): configuración de buzones y de la aplicación
        # OAuth que la entidad registró ante Google o Microsoft.
        ("/api/v1/email/", app_state.document_service_url),
        ("/api/v1/envios/", app_state.document_service_url),
        # Terceros: catálogo de personas y entidades destinatarias, y la
        # cascada DIVIPOLA que las geolocaliza (RF-RAD-05). SIN barra final
        # —igual que `anulaciones`— porque `/api/v1/terceros` es una ruta
        # exacta (el listado y el alta) y con barra el prefijo no casaría con
        # ella: el alta de un destinatario respondía `route_not_found`.
        ("/api/v1/terceros", app_state.document_service_url),
        # Cola del aprobador de anulaciones (recurso propio, no una subruta
        # de un documento). SIN barra final: la ruta es `/api/v1/anulaciones`
        # exacta, y con barra el prefijo no casaría con ella.
        ("/api/v1/anulaciones", app_state.document_service_url),
        # Cola del aprobador de desclasificaciones (C4, ADR-039): mismo criterio,
        # recurso propio y SIN barra final.
        ("/api/v1/desclasificaciones", app_state.document_service_url),
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
        # E04/RF-TRD-07: lotes de eliminación documental. Recurso propio, no
        # un apéndice de /trd -- es un procedimiento con ciclo de vida.
        ("/api/v1/eliminacion/", app_state.archive_service_url),
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
        ("/api/v1/fuid.xml", app_state.archive_service_url),
        ("/api/v1/transferencias/", app_state.archive_service_url),
        # storage-service — SOLO /upload se expone (el frontend sube directo).
        # D-02 (remediación no-read-up en anexos, RF-SEG-08): los endpoints
        # que sirven bytes/URL/metadata de un archivo (download presigned,
        # content, metadata) quedan protegidos por X-Internal-Token en
        # storage-service y NO se enrutan desde el exterior — la descarga
        # pasa SIEMPRE por el mediador de document-service
        # (`GET /api/v1/documents/{id}/anexos/{file_id}/download`).
        ("/api/v1/storage/upload", app_state.storage_service_url),
        # preservación (E10) — LISTA DE ADMISIÓN de subrutas, no el prefijo
        # entero (AREA-08-16). `/proteger-indice`, `/proteger-artefacto`, `/aip`,
        # `/*-internal` y `/legal-hold-internal/*` (`/fijar` y `/{hold_id}/levantar`,
        # ADR-043) son actos de SISTEMA: los
        # invoca archive-service (con X-Internal-Token) y NO se enrutan desde el
        # exterior. Con el prefijo completo, un JWT de usuario con
        # USUA_PERM_EXPEDIENTE:3 ocupaba el singleton WORM irreversible de
        # cualquier expediente con un file_id arbitrario. Una subruta nueva de
        # storage queda cerrada hasta que se admita aquí a propósito.
        ("/api/v1/preservacion/estado", app_state.storage_service_url),
        ("/api/v1/preservacion/plan", app_state.storage_service_url),
        ("/api/v1/preservacion/eventos", app_state.storage_service_url),
        ("/api/v1/preservacion/migrar-formato", app_state.storage_service_url),
        ("/api/v1/preservacion/migraciones/", app_state.storage_service_url),
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
        # CC-02: aceptación humana de una sugerencia del RAG (solo quien la generó).
        ("/api/v1/knowledge/suggestions/", app_state.knowledge_service_url),
        # Admin DLQ (AREA-10-10): inspección y reproceso del dead-letter de la
        # ingesta. Cada ruta exige PERM_DLQ_ADMIN en knowledge-service; /ingest
        # sigue sin enrutarse.
        ("/api/v1/knowledge/admin/", app_state.knowledge_service_url),
    ]
    # Límite de segmento: un prefijo sin barra final casa solo la ruta exacta o
    # sus subrutas (`/preservacion/eventos` NO casa `/preservacion/eventos-x`).
    for prefix, base_url in routing:
        if prefix.endswith("/"):
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                return base_url
        elif path == prefix or path.startswith(prefix + "/"):
            return base_url
    return None


async def _handle_proxy(request: Request, path: str) -> Response:
    ruta = _normalize_path("/" + path)
    upstream_base = (
        _route_to_upstream(ruta, request.app.state) if ruta is not None else None
    )

    if upstream_base is None or ruta is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "route_not_found",
                "detail": f"Ninguna ruta del sistema corresponde a /{path}",
                "status": 404,
            },
        )

    upstream_url = f"{upstream_base.rstrip('/')}{ruta}"

    extra_headers: dict[str, str] = {}
    auth_headers = getattr(request.state, "auth_headers", None)
    if auth_headers:
        extra_headers.update(auth_headers)

    http_client: httpx.AsyncClient = request.app.state.http_client

    # El asistente (E18) necesita un timeout mucho mayor que el global (loop LLM).
    timeout: float | None = None
    if upstream_base == request.app.state.mcp_service_url:
        timeout = request.app.state.assistant_proxy_timeout

    # Las rutas anónimas llevan su propio tope, mucho más estrecho: el general
    # está dimensionado para el ZIP de importación, que exige autenticación.
    max_body_bytes = request.app.state.max_request_body_bytes
    if request.url.path.startswith("/api/v1/public/"):
        max_body_bytes = min(max_body_bytes, request.app.state.max_public_body_bytes)
    elif request.url.path.startswith("/api/v1/storage/"):
        # La subida de anexos no necesita el tope de la importación: acotarla
        # aquí evita que cada rechazo por tamaño cueste medio giga de disco en
        # el contenedor de destino, escrito antes de que nadie pueda mirarlo.
        max_body_bytes = min(max_body_bytes, request.app.state.max_upload_body_bytes)

    try:
        return await proxy_request(
            http_client=http_client,
            upstream_url=upstream_url,
            request=request,
            extra_headers=extra_headers,
            timeout=timeout,
            max_body_bytes=max_body_bytes,
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
