# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Catálogo de tipos documentales (3.er nivel TRD, E03/RF-MET-07).

CRUD de `tipos_documentales`: el tipo cuelga de una serie TRD, se marca
obligatorio/opcional y referencia la plantilla de metadatos asociada.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.database import get_db_conn
from app.repositories.archive_repository import ArchiveRepository
from app.schemas.archive import (
    TipoDocumentalCreate,
    TipoDocumentalResponse,
    TipoDocumentalUpdate,
)

router = APIRouter(prefix="/api/v1/tipos-documentales", tags=["tipos-documentales"])


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


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": "Tipo documental no encontrado", "status": 404},
    )


@router.post("", response_model=TipoDocumentalResponse, status_code=status.HTTP_201_CREATED)
async def create_tipo(
    payload: TipoDocumentalCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TipoDocumentalResponse:
    repo = ArchiveRepository(conn, tenant_slug)
    try:
        row = await repo.create_tipo_documental(payload.model_dump())
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"Ya existe el tipo '{payload.code}' en esa serie",
                "status": 409,
            },
        )
    return TipoDocumentalResponse(**dict(row))


@router.get("", response_model=list[TipoDocumentalResponse])
async def list_tipos(
    trd_serie_id: uuid.UUID | None = Query(default=None),
    activo: bool | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TipoDocumentalResponse]:
    repo = ArchiveRepository(conn, tenant_slug)
    rows = await repo.list_tipos_documentales(trd_serie_id=trd_serie_id, activo=activo)
    return [TipoDocumentalResponse(**dict(r)) for r in rows]


@router.get("/{tipo_id}", response_model=TipoDocumentalResponse)
async def get_tipo(
    tipo_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TipoDocumentalResponse:
    row = await ArchiveRepository(conn, tenant_slug).get_tipo_documental(tipo_id)
    if row is None:
        raise _not_found()
    return TipoDocumentalResponse(**dict(row))


@router.patch("/{tipo_id}", response_model=TipoDocumentalResponse)
async def update_tipo(
    tipo_id: uuid.UUID,
    payload: TipoDocumentalUpdate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TipoDocumentalResponse:
    repo = ArchiveRepository(conn, tenant_slug)
    row = await repo.update_tipo_documental(tipo_id, payload.model_dump(exclude_unset=True))
    if row is None:
        raise _not_found()
    return TipoDocumentalResponse(**dict(row))


@router.delete("/{tipo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tipo(
    tipo_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    if not await ArchiveRepository(conn, tenant_slug).delete_tipo_documental(tipo_id):
        raise _not_found()
