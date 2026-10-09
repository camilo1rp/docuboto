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
radicados por dependencia. Las escrituras exigen `USUA_PERM_ADMIN`
(`app/core/authz.py`); los GET quedan solo autenticados — E01 y otros
consumidores internos los necesitan para resolver dependencias por código.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core import audit as audit_core
from app.core.authz import require_permission
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
        detail={"error": "not_found", "detail": "No existe la dependencia", "status": 404},
    )


def _bad_parent() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": "invalid_parent", "detail": "La dependencia padre indicada no existe", "status": 400},
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


@router.post(
    "",
    response_model=DependenciaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def create_dependencia(
    payload: DependenciaCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DependenciaResponse:
    repo = DependenciaRepository(conn)
    try:
        async with conn.transaction():
            row = await repo.insert(
                codigo=payload.codigo,
                sigla=payload.sigla,
                nombre=payload.nombre,
                parent_id=payload.parent_id,
                depe_rad_tp2=payload.depe_rad_tp2,
                activo=payload.activo,
            )
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="dependencia.creada",
                object_type="dependencia",
                object_ref=str(row["id"]),
                actor=audit_core.actor_from(request),
                payload={"codigo": row["codigo"], "sigla": row["sigla"], "nombre": row["nombre"]},
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.UniqueViolationError:
        raise _conflict(f"Ya existe una dependencia con el código «{payload.codigo}»")
    except asyncpg.ForeignKeyViolationError:
        raise _bad_parent()
    return DependenciaResponse(**dict(row))


async def _exigir_sin_tramite_vivo(repo: DependenciaRepository, nombre: str, accion: str) -> None:
    """409 si pasos vivos o reglas de flujo referencian la dependencia por nombre."""
    vivos = await repo.tramites_vivos(nombre)
    if vivos["pasos_vivos"] > 0 or vivos["reglas"] > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dependencia_con_tramite_en_curso",
                "detail": (
                    "La dependencia tiene trámite en curso o reglas de flujo que la "
                    f"referencian por nombre: reasigne o cierre el trámite antes de {accion}"
                ),
                "pasos_vivos": vivos["pasos_vivos"],
                "reglas": vivos["reglas"],
                "status": 409,
            },
        )


@router.patch(
    "/{dependencia_id}",
    response_model=DependenciaResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def update_dependencia(
    dependencia_id: uuid.UUID,
    payload: DependenciaUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DependenciaResponse:
    if payload.parent_id == dependencia_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_parent",
                "detail": "Una dependencia no puede ser su propia dependencia padre",
                "status": 400,
            },
        )
    repo = DependenciaRepository(conn)
    fields = payload.model_dump(exclude_unset=True)
    try:
        async with conn.transaction():
            actual = await repo.get_for_update(dependencia_id)
            if actual is None:
                raise _not_found()
            cambia_nombre = "nombre" in fields and fields["nombre"] != actual["nombre"]
            desactiva = fields.get("activo") is False and actual["activo"] is not False
            if cambia_nombre or desactiva:
                await _exigir_sin_tramite_vivo(repo, actual["nombre"], "renombrarla o desactivarla")
            row = await repo.update(dependencia_id, fields=fields)
            if row is None:
                raise _not_found()
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="dependencia.actualizada",
                object_type="dependencia",
                object_ref=str(dependencia_id),
                actor=audit_core.actor_from(request),
                payload={
                    "fields": fields,
                    "antes": {"nombre": actual["nombre"], "activo": actual["activo"]},
                    "despues": {"nombre": row["nombre"], "activo": row["activo"]},
                },
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.UniqueViolationError:
        raise _conflict("Ya existe un registro con ese código")
    except asyncpg.ForeignKeyViolationError:
        raise _bad_parent()
    return DependenciaResponse(**dict(row))


@router.delete(
    "/{dependencia_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def delete_dependencia(
    dependencia_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = DependenciaRepository(conn)
    if await repo.has_children(dependencia_id):
        raise _conflict("La dependencia tiene dependencias hijas: reasígnelas o elimínelas primero")
    try:
        async with conn.transaction():
            actual = await repo.get_for_update(dependencia_id)
            if actual is None:
                raise _not_found()
            await _exigir_sin_tramite_vivo(repo, actual["nombre"], "eliminarla")
            deleted = await repo.delete(dependencia_id)
            if not deleted:
                raise _not_found()
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="dependencia.eliminada",
                object_type="dependencia",
                object_ref=str(dependencia_id),
                actor=audit_core.actor_from(request),
                payload={"antes": {"nombre": actual["nombre"], "activo": actual["activo"]}},
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.ForeignKeyViolationError:
        raise _conflict("Otros registros hacen referencia a la dependencia")
