# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.archive import (
    RetentionSchedule,
    TrdSerieCreate,
    TrdSerieResponse,
    TrdSerieUpdate,
)
from app.services.archive_service import ArchiveService

router = APIRouter(prefix="/api/v1/trd", tags=["trd"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "X-Tenant-Slug header is required",
                "status": 400,
            },
        )
    return x_tenant_slug


def _optional_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    if not x_user_id:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "X-User-Id must be a valid UUID",
                "status": 400,
            },
        )


@router.post(
    "",
    response_model=TrdSerieResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a TRD documentary series",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def create_trd_serie(
    payload: TrdSerieCreate,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.create_trd_serie(payload, actor)


@router.get(
    "",
    response_model=list[TrdSerieResponse],
    summary="List TRD series",
)
async def list_trd_series(
    response: Response,
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TrdSerieResponse]:
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_trd_series(is_active=is_active, page=page, size=size)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{serie_id}/retention",
    response_model=RetentionSchedule,
    summary="Compute retention/disposition schedule for a closed expediente (E04)",
)
async def compute_retention(
    serie_id: uuid.UUID,
    closed_at: date = Query(..., description="Fecha de cierre del expediente (ISO date)"),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RetentionSchedule:
    service = ArchiveService(conn, tenant_slug)
    return await service.compute_retention(serie_id, closed_at)


@router.get(
    "/{serie_id}",
    response_model=TrdSerieResponse,
    summary="Get TRD serie by ID",
)
async def get_trd_serie(
    serie_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_trd_serie(serie_id)


@router.patch(
    "/{serie_id}",
    response_model=TrdSerieResponse,
    summary="Update TRD serie (code is immutable)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def update_trd_serie(
    serie_id: uuid.UUID,
    payload: TrdSerieUpdate,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.update_trd_serie(serie_id, payload, actor)
