# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de catálogos de referencia por tenant (E14).

Catálogos disponibles (segmento `{catalogo}`): tipos-identificacion,
tipos-remitente, medios-recepcion, tipos-anexo. El nombre se resuelve contra la
whitelist `CATALOGS`; cualquier otro valor → 404.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.core.database import get_tenant_conn
from app.repositories.catalogos_repository import CATALOGS, CatalogoRepository
from app.schemas.catalogos import (
    CatalogoItemCreate,
    CatalogoItemResponse,
    CatalogoItemUpdate,
)

router = APIRouter(prefix="/api/v1/catalogos", tags=["catalogos"])


def _resolve(catalogo: str) -> str:
    table = CATALOGS.get(catalogo)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "unknown_catalog",
                "detail": f"Unknown catalog '{catalogo}'. Available: {sorted(CATALOGS)}",
                "status": 404,
            },
        )
    return table


@router.get("", response_model=list[str])
async def list_catalogos() -> list[str]:
    """Nombres de catálogos disponibles."""
    return sorted(CATALOGS)


@router.get("/{catalogo}", response_model=list[CatalogoItemResponse])
async def list_items(
    catalogo: str = Path(...),
    activo: bool | None = Query(None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[CatalogoItemResponse]:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    return [CatalogoItemResponse(**dict(r)) for r in await repo.list(activo=activo)]


@router.post(
    "/{catalogo}", response_model=CatalogoItemResponse, status_code=status.HTTP_201_CREATED
)
async def create_item(
    payload: CatalogoItemCreate,
    catalogo: str = Path(...),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> CatalogoItemResponse:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    try:
        row = await repo.insert(codigo=payload.codigo, nombre=payload.nombre, activo=payload.activo)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"codigo {payload.codigo} already exists",
                "status": 409,
            },
        )
    return CatalogoItemResponse(**dict(row))


@router.patch("/{catalogo}/{item_id}", response_model=CatalogoItemResponse)
async def update_item(
    payload: CatalogoItemUpdate,
    catalogo: str = Path(...),
    item_id: uuid.UUID = Path(...),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> CatalogoItemResponse:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    row = await repo.update(item_id, fields=payload.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Item not found", "status": 404},
        )
    return CatalogoItemResponse(**dict(row))


@router.delete("/{catalogo}/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    catalogo: str = Path(...),
    item_id: uuid.UUID = Path(...),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    if not await repo.delete(item_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Item not found", "status": 404},
        )
