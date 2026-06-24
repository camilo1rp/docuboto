# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.core.database import get_db_conn
from app.schemas.tenant import TenantCreate, TenantResponse, TenantUpdate
from app.services.tenant_service import (
    DuplicateCodeError,
    DuplicateSlugError,
    TenantNotFoundError,
    TenantService,
)

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tenant (institution)",
)
async def create_tenant(
    payload: TenantCreate,
    response: Response,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TenantResponse:
    service = TenantService(conn)
    try:
        return await service.create(payload)
    except DuplicateSlugError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "duplicate_slug",
                "detail": f"A tenant with slug '{payload.slug}' already exists",
                "status": 409,
            },
        )
    except DuplicateCodeError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "duplicate_code",
                "detail": f"A tenant with code '{payload.code}' already exists",
                "status": 409,
            },
        )


@router.get(
    "",
    response_model=list[TenantResponse],
    summary="List tenants with optional status filter",
)
async def list_tenants(
    response: Response,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TenantResponse]:
    service = TenantService(conn)
    tenants, total = await service.list(status=status_filter, page=page, size=size)
    response.headers["X-Total-Count"] = str(total)
    return tenants


@router.get(
    "/by-slug/{slug}",
    response_model=TenantResponse,
    summary="Get tenant by slug — used by other services to validate JWT tenant_slug",
)
async def get_tenant_by_slug(
    slug: str,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TenantResponse:
    service = TenantService(conn)
    try:
        return await service.get_by_slug(slug)
    except TenantNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "tenant_not_found",
                "detail": f"Tenant with slug '{slug}' not found",
                "status": 404,
            },
        )


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Get tenant by UUID",
)
async def get_tenant(
    tenant_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TenantResponse:
    service = TenantService(conn)
    try:
        return await service.get_by_id(tenant_id)
    except TenantNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "tenant_not_found",
                "detail": f"Tenant with id '{tenant_id}' not found",
                "status": 404,
            },
        )


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Update tenant name and/or status — slug and code are immutable",
)
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TenantResponse:
    service = TenantService(conn)
    try:
        return await service.update(tenant_id, payload)
    except TenantNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "tenant_not_found",
                "detail": f"Tenant with id '{tenant_id}' not found",
                "status": 404,
            },
        )
