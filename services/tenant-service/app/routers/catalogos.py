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
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.core import audit as audit_core
from app.core.authz import require_permission
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
                "detail": f"No existe el catálogo «{catalogo}». Disponibles: {sorted(CATALOGS)}",
                "status": 404,
            },
        )
    return table


@router.get("", response_model=list[str])
async def list_catalogos() -> list[str]:
    """Nombres de catálogos disponibles."""
    return sorted(CATALOGS)


def _exigir_ambito_soportado(repo: CatalogoRepository, catalogo: str) -> None:
    """422 si se pide `ambito` sobre un catálogo que no distingue acto.

    Ignorarlo en silencio sería peor que fallar: el llamante recibiría la lista
    ENTERA creyendo que viene filtrada, y ofrecería causales de anulación como
    causales de devolución sin que nada lo delate.
    """
    if not repo.soporta_ambito:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "ambito_no_aplica",
                "detail": f"El catálogo «{catalogo}» no distingue ámbito",
                "status": 422,
            },
        )


@router.get("/{catalogo}", response_model=list[CatalogoItemResponse])
async def list_items(
    catalogo: str = Path(...),
    activo: bool | None = Query(None),
    ambito: str | None = Query(None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[CatalogoItemResponse]:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    if ambito is not None:
        _exigir_ambito_soportado(repo, catalogo)
    return [
        CatalogoItemResponse(**dict(r)) for r in await repo.list(activo=activo, ambito=ambito)
    ]


@router.post(
    "/{catalogo}",
    response_model=CatalogoItemResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def create_item(
    payload: CatalogoItemCreate,
    request: Request,
    catalogo: str = Path(...),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> CatalogoItemResponse:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    if payload.ambito is not None:
        _exigir_ambito_soportado(repo, catalogo)
    try:
        async with conn.transaction():
            row = await repo.insert(
                codigo=payload.codigo,
                nombre=payload.nombre,
                activo=payload.activo,
                ambito=payload.ambito,
            )
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="catalogo.item_creado",
                object_type="catalogo_item",
                object_ref=f"{catalogo}:{row['codigo']}",
                actor=audit_core.actor_from(request),
                payload={"catalogo": catalogo, "codigo": row["codigo"], "nombre": row["nombre"]},
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"Ya existe un registro con el código «{payload.codigo}»",
                "status": 409,
            },
        )
    return CatalogoItemResponse(**dict(row))


@router.patch(
    "/{catalogo}/{item_id}",
    response_model=CatalogoItemResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def update_item(
    payload: CatalogoItemUpdate,
    request: Request,
    catalogo: str = Path(...),
    item_id: uuid.UUID = Path(...),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> CatalogoItemResponse:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    campos = payload.model_dump(exclude_unset=True)
    if "ambito" in campos:
        _exigir_ambito_soportado(repo, catalogo)
    async with conn.transaction():
        row = await repo.update(item_id, fields=campos)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "detail": "No existe el elemento", "status": 404},
            )
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="catalogo.item_actualizado",
            object_type="catalogo_item",
            object_ref=f"{catalogo}:{item_id}",
            actor=audit_core.actor_from(request),
            payload={"catalogo": catalogo, "fields": campos},
            canal=audit_core.CANAL_PETICION,
        )
    return CatalogoItemResponse(**dict(row))


@router.delete(
    "/{catalogo}/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def delete_item(
    request: Request,
    catalogo: str = Path(...),
    item_id: uuid.UUID = Path(...),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = CatalogoRepository(conn, _resolve(catalogo))
    async with conn.transaction():
        if not await repo.delete(item_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "detail": "No existe el elemento", "status": 404},
            )
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="catalogo.item_eliminado",
            object_type="catalogo_item",
            object_ref=f"{catalogo}:{item_id}",
            actor=audit_core.actor_from(request),
            payload={"catalogo": catalogo},
            canal=audit_core.CANAL_PETICION,
        )
