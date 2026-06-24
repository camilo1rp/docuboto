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

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.clients.tenant_client import TenantServiceClient, TenantServiceError
from app.core.clearance import resolve_caller_clearance
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.document import (
    DispositionResponse,
    DispositionUpdate,
    DocumentCreate,
    DocumentResponse,
    DocumentUpdate,
)
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentService,
    InvalidDependenciaError,
    InvalidTenantError,
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
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        return await service.get_by_tracking_number(tracking_number, clearance)
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
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        return await service.get_by_id(document_id, clearance)
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
    "/{document_id}/disposition",
    response_model=DispositionResponse,
    summary="Get document disposition metadata (RF-MET-08)",
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
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Update mutable fields of a radicado — tracking_number is immutable",
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
