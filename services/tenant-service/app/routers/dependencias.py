# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de dependencias (organigrama) por tenant (E14).

CRUD jerárquico. E01 consume `GET /dependencias/by-codigo/{codigo}` para numerar
radicados por dependencia. El gate de permiso del llamante se añade con el JWT
enriquecido (E08); hoy el api-gateway media el acceso.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.database import get_tenant_conn
from app.repositories.dependencias_repository import DependenciaRepository
from app.schemas.dependencias import (
    DependenciaCreate,
    DependenciaNode,
    DependenciaResponse,
    DependenciaUpdate,
)

router = APIRouter(prefix="/api/v1/dependencias", tags=["dependencias"])


def _conflict(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "conflict", "detail": detail, "status": 409},
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": "Dependencia not found", "status": 404},
    )


def _bad_parent() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": "invalid_parent", "detail": "parent_id does not exist", "status": 400},
    )


@router.get("", response_model=list[DependenciaResponse])
async def list_dependencias(
    activo: bool | None = Query(None),
    parent_id: uuid.UUID | None = Query(None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DependenciaResponse]:
    repo = DependenciaRepository(conn)
    rows = await repo.list(activo=activo, parent_id=parent_id)
    return [DependenciaResponse(**dict(r)) for r in rows]


@router.get("/tree", response_model=list[DependenciaNode])
async def dependencias_tree(
    activo: bool | None = Query(None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DependenciaNode]:
    """Árbol jerárquico completo (raíces con sus hijos anidados)."""
    repo = DependenciaRepository(conn)
    rows = await repo.list(activo=activo)
    nodes: dict[uuid.UUID, DependenciaNode] = {
        r["id"]: DependenciaNode(**dict(r), children=[]) for r in rows
    }
    roots: list[DependenciaNode] = []
    for node in nodes.values():
        parent = nodes.get(node.parent_id) if node.parent_id else None
        if parent is not None:
            parent.children.append(node)
        else:
            # Sin padre, o el padre quedó fuera del filtro → se trata como raíz.
            roots.append(node)
    return roots


@router.get("/by-codigo/{codigo}", response_model=DependenciaResponse)
async def get_by_codigo(
    codigo: int, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> DependenciaResponse:
    repo = DependenciaRepository(conn)
    row = await repo.get_by_codigo(codigo)
    if row is None:
        raise _not_found()
    return DependenciaResponse(**dict(row))


@router.get("/{dependencia_id}", response_model=DependenciaResponse)
async def get_dependencia(
    dependencia_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> DependenciaResponse:
    repo = DependenciaRepository(conn)
    row = await repo.get(dependencia_id)
    if row is None:
        raise _not_found()
    return DependenciaResponse(**dict(row))


@router.post("", response_model=DependenciaResponse, status_code=status.HTTP_201_CREATED)
async def create_dependencia(
    payload: DependenciaCreate, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> DependenciaResponse:
    repo = DependenciaRepository(conn)
    try:
        row = await repo.insert(
            codigo=payload.codigo,
            sigla=payload.sigla,
            nombre=payload.nombre,
            parent_id=payload.parent_id,
            depe_rad_tp2=payload.depe_rad_tp2,
            activo=payload.activo,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict(f"codigo {payload.codigo} already exists")
    except asyncpg.ForeignKeyViolationError:
        raise _bad_parent()
    return DependenciaResponse(**dict(row))


@router.patch("/{dependencia_id}", response_model=DependenciaResponse)
async def update_dependencia(
    dependencia_id: uuid.UUID,
    payload: DependenciaUpdate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DependenciaResponse:
    if payload.parent_id == dependencia_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_parent",
                "detail": "dependencia cannot be its own parent",
                "status": 400,
            },
        )
    repo = DependenciaRepository(conn)
    fields = payload.model_dump(exclude_unset=True)
    try:
        row = await repo.update(dependencia_id, fields=fields)
    except asyncpg.UniqueViolationError:
        raise _conflict("codigo already exists")
    except asyncpg.ForeignKeyViolationError:
        raise _bad_parent()
    if row is None:
        raise _not_found()
    return DependenciaResponse(**dict(row))


@router.delete("/{dependencia_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dependencia(
    dependencia_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> None:
    repo = DependenciaRepository(conn)
    if await repo.has_children(dependencia_id):
        raise _conflict("dependencia has children; reassign or delete them first")
    try:
        deleted = await repo.delete(dependencia_id)
    except asyncpg.ForeignKeyViolationError:
        raise _conflict("dependencia is referenced by other records")
    if not deleted:
        raise _not_found()
