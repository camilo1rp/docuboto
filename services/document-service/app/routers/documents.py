# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Document router — radicado CRUD endpoints.

Tenant context comes from X-Tenant-Slug (injected by the api-gateway after JWT
validation). The get_tenant_conn dependency validates the slug and sets the
connection search_path to tenant_{slug} (ADR-012); X-User-Id carries the
authenticated user's UUID for the registered_by field.

Route order: /by-tracking/{tracking_number} is declared before /{document_id}
to avoid FastAPI matching the literal string "by-tracking" as a UUID.
"""

import uuid
from typing import Any
from urllib.parse import quote

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.clients import storage_client
from app.clients.tenant_client import TenantServiceClient, TenantServiceError
from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.document import (
    AnexoResponse,
    DispositionResponse,
    DispositionUpdate,
    DocumentCreate,
    DocumentResponse,
    DocumentUpdate,
    ReclassifyRequest,
    ReclassifyResponse,
)
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentService,
    InvalidDependenciaError,
    InvalidTenantError,
    ReclassifyForbiddenError,
)
from app.services.metadata_service import MetadataValidationError

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"


def _tenant_slug(request: Request) -> str:
    # get_tenant_conn ya garantiza que el header está presente y es válido.
    return request.headers.get(_TENANT_HEADER, "")


def _get_registered_by(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _content_disposition(filename: str) -> str:
    """Cabecera `Content-Disposition` a prueba de inyección (RFC 6266).

    `filename` lo controla el cliente en la subida; sin sanear, un `"` rompe la
    quoted-string (inyecta parámetros) y un CR/LF revienta la cabecera. Se emite
    un fallback ASCII entrecomillado saneado + `filename*=UTF-8''<pct-encoded>`
    para el nombre real (Unicode).
    """
    ascii_fallback = (
        filename.encode("ascii", "ignore")
        .decode("ascii")
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
        .strip()
        or "download"
    )
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Canal de origen de la petición (X-Canal, inyectado server-side por el
    api-gateway, D-03; excluido del whitelist de client request headers en
    `api-gateway/app/core/proxy.py::FORWARDED_REQUEST_HEADERS`, así que un
    cliente no puede forjarlo). Ausente => 'system' (no se audita como
    consulta de un usuario real). Gemelo de
    `archive-service/app/routers/expedientes.py::_optional_canal`."""
    return x_canal or "system"


def _build_service(
    conn: asyncpg.Connection,
    slug: str,
    tenant_client: TenantServiceClient | None = None,
    redis: Any | None = None,
) -> DocumentService:
    try:
        return DocumentService(conn, slug, tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new radicado (document)",
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def create_document(
    payload: DocumentCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: TenantServiceClient = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request), tenant_client, redis)
    try:
        return await service.create(payload, _get_registered_by(request))
    except MetadataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "metadata_validation_failed",
                "detail": str(exc),
                "status": 422,
            },
        )
    except InvalidDependenciaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_dependencia",
                "detail": str(exc),
                "status": 400,
            },
        )
    except TenantServiceError as exc:
        # El plazo (RF-RAD-04) o la dependencia no se pudieron resolver vía HTTP.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "tenant_service_unavailable",
                "detail": str(exc),
                "status": 502,
            },
        )


@router.get(
    "",
    response_model=list[DocumentResponse],
    summary="List radicados with optional filters",
)
async def list_documents(
    request: Request,
    response: Response,
    doc_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> list[DocumentResponse]:
    service = _build_service(conn, _tenant_slug(request))
    # Filtro por campo de metadato: ?meta.<campo>=valor (E03, índice GIN).
    metadata_filter = {
        k[len("meta.") :]: v
        for k, v in request.query_params.items()
        if k.startswith("meta.") and k != "meta."
    }
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    documents, total = await service.list(
        doc_type=doc_type,
        status=status_filter,
        max_clearance=clearance,
        metadata_filter=metadata_filter or None,
        page=page,
        size=size,
        user_id=_get_registered_by(request),
        canal=canal,
    )
    response.headers["X-Total-Count"] = str(total)
    return documents


@router.get(
    "/by-tracking/{tracking_number:path}",
    response_model=DocumentResponse,
    summary="Get radicado by tracking number (e.g. 2024-ICETEX-E-001234)",
)
async def get_document_by_tracking(
    tracking_number: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        return await service.get_by_tracking_number(
            tracking_number, clearance, user_id=_get_registered_by(request), canal=canal
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": f"Document with tracking number '{tracking_number}' not found",
                "status": 404,
            },
        )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get radicado by UUID",
)
async def get_document(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        return await service.get_by_id(
            document_id, clearance, user_id=_get_registered_by(request), canal=canal
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": f"Document '{document_id}' not found",
                "status": 404,
            },
        )


@router.get(
    "/{document_id}/anexos",
    response_model=list[AnexoResponse],
    summary="List a radicado's attachments, gated by clearance (RF-SEG-08)",
)
async def list_anexos(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[AnexoResponse]:
    """Lista los anexos de un radicado (metadatos: nombre, tamaño, tipo…) para
    poblar el panel de anexos; el fichero se obtiene con el endpoint de
    descarga mediada. No-read-up: un radicado inexistente o por encima del
    clearance del llamante devuelve el MISMO 404 neutro (no-oráculo).
    """
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        anexos = await service.list_anexos(document_id, clearance)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    return [AnexoResponse.model_validate(a) for a in anexos]


@router.get(
    "/{document_id}/anexos/{file_id}/download",
    summary="Download an attachment, mediated by clearance (RF-SEG-08, D-02)",
)
async def download_anexo(
    document_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> StreamingResponse:
    """Único punto de descarga de anexos hacia el exterior.

    document-service MEDIA la descarga: resuelve el clearance del llamante
    contra el `nivel_seguridad` VIGENTE del radicado (sin lag — nunca se
    denormaliza la clasificación al anexo/`files`) y verifica que `file_id`
    es efectivamente un anexo de `document_id`. Solo entonces pide los BYTES
    a storage-service (nunca una presigned URL — el cliente podría reenviarla
    y saltarse este gate) y los retransmite con streaming real.

    Todos los caminos de denegación devuelven el MISMO 404 neutro: radicado
    inexistente, sobre-clearance (no-read-up) y `file_id` ajeno al radicado
    quedan indistinguibles para el llamante — cierra el oráculo cruzado que
    tenía la descarga directa a storage-service.
    """
    tenant_slug = _tenant_slug(request)
    service = _build_service(conn, tenant_slug)
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        anexo = await service.get_anexo_for_download(
            document_id,
            file_id,
            clearance,
            user_id=_get_registered_by(request),
            canal=canal,
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": f"Document '{document_id}' not found",
                "status": 404,
            },
        )

    try:
        stream = await storage_client.open_content_stream(
            tenant_slug=tenant_slug, file_id=anexo.file_id
        )
    except storage_client.StorageContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "storage_unavailable",
                "detail": str(exc),
                "status": 503,
            },
        )
    if stream is None:
        # El objeto ya no existe en storage (purgado) aunque el anexo siga
        # referenciado — mismo 404 neutro, no se distingue de "no autorizado".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": f"Document '{document_id}' not found",
                "status": 404,
            },
        )

    headers = {"Content-Disposition": _content_disposition(anexo.filename)}
    return StreamingResponse(
        stream.aiter_bytes(),
        media_type=anexo.mime_type or "application/octet-stream",
        headers=headers,
        background=BackgroundTask(stream.aclose),
    )


@router.get(
    "/{document_id}/disposition",
    response_model=DispositionResponse,
    summary="Get document disposition metadata (RF-MET-08)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_disposition(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DispositionResponse:
    service = _build_service(conn, _tenant_slug(request))
    try:
        disp = await service.get_disposition(document_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    return DispositionResponse(radicado_id=document_id, disposition=disp)


@router.patch(
    "/{document_id}/disposition",
    response_model=DispositionResponse,
    summary="Set/update document disposition metadata (RF-MET-08)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def set_disposition(
    document_id: uuid.UUID,
    payload: DispositionUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DispositionResponse:
    service = _build_service(conn, _tenant_slug(request))
    try:
        disp = await service.set_disposition(document_id, payload)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    return DispositionResponse(radicado_id=document_id, disposition=disp)


@router.patch(
    "/{document_id}/security-level",
    response_model=ReclassifyResponse,
    summary="Reclassify the security level of a radicado (E08, RF-SEG-08, Ley 594 art. 28)",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR"))],
)
async def reclassify_security_level(
    document_id: uuid.UUID,
    payload: ReclassifyRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    redis: Any = Depends(get_redis),
) -> ReclassifyResponse:
    """Change the security level of a radicado.

    Enforces RF-SEG-08 clearance rules:
    - **no-read-up**: returns 404 (neutral) when the actor's clearance is below the
      current level, so under-clearance users cannot distinguish "not found" from
      "exists but classified above your level".
    - **no-write-up**: returns 403 when nuevo_nivel exceeds the actor's own clearance.
    - **idempotent**: 200 with the current state if the level is already at the
      requested value — safe to retry without duplicate audit entries or events.

    Audit: nivel_anterior, nivel_nuevo, and motivo are recorded in audit_log
    (Ley 594/2000 art. 28 — trazabilidad del ciclo de vida del documento).
    """
    service = _build_service(conn, _tenant_slug(request), redis=redis)
    actor = _get_registered_by(request)
    try:
        result = await service.reclassify(
            document_id,
            nuevo_nivel=payload.nivel_seguridad,
            motivo=payload.motivo,
            actor=actor,
            causal_reserva=payload.causal_reserva,
            fundamento_juridico=payload.fundamento_juridico,
            plazo_reserva_meses=payload.plazo_reserva_meses,
        )
        return ReclassifyResponse(**result)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": f"Document '{document_id}' not found",
                "status": 404,
            },
        )
    except ReclassifyForbiddenError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "reclassify_forbidden",
                "detail": "Cannot reclassify above your own clearance level",
                "status": 403,
            },
        )


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Update mutable fields of a radicado — tracking_number is immutable",
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request))
    try:
        return await service.update(document_id, payload)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": f"Document '{document_id}' not found",
                "status": 404,
            },
        )
