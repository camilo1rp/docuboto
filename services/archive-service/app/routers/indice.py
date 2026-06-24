# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Índice electrónico del expediente (E15, Acuerdo AGN 001/2024).

Lectura del índice vigente / por versión / XML / historial, y `rebuild` (admin).
La firma al cierre (F3) y la verificación de fixity contra storage (T-08/T-14)
son incrementos posteriores.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.core.database import get_db_conn
from app.schemas.archive import (
    IndiceResponse,
    IndiceVerifyResponse,
    IndiceVersionSummary,
    RebuildResult,
)
from app.services.index_service import IndexService

router = APIRouter(prefix="/api/v1/expedientes", tags=["indice"])


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


@router.get("/{expediente_id}/indice", response_model=IndiceResponse)
async def get_indice(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceResponse:
    return await IndexService(conn, tenant_slug).get_indice(expediente_id, version)


@router.get("/{expediente_id}/indice.xml", response_class=PlainTextResponse)
async def get_indice_xml(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> Response:
    xml = await IndexService(conn, tenant_slug).get_xml(expediente_id, version)
    return Response(content=xml, media_type="application/xml")


@router.get("/{expediente_id}/indice/versions", response_model=list[IndiceVersionSummary])
async def list_indice_versions(
    expediente_id: uuid.UUID,
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[IndiceVersionSummary]:
    items, total = await IndexService(conn, tenant_slug).list_versions(
        expediente_id, page=page, size=size
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/{expediente_id}/indice/verify", response_model=IndiceVerifyResponse)
async def verify_indice(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceVerifyResponse:
    return await IndexService(conn, tenant_slug).verify(expediente_id, version)


@router.post("/{expediente_id}/indice/rebuild", response_model=RebuildResult)
async def rebuild_indice(
    expediente_id: uuid.UUID,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RebuildResult:
    generado_por = request.headers.get("X-Username") or "system"
    return await IndexService(conn, tenant_slug).regenerate(
        expediente_id, generado_por=generado_por
    )
