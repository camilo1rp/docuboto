# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Administración de URD (Usuario-Rol-Dependencia) (E08).

Gated con `USUA_PERM_ADMIN` (ADR-013). El cambio de contexto del propio usuario
vive en routers/context.py (no es admin).
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.authz import Caller, get_caller, require_permission
from app.core.database import get_tenant_conn
from app.schemas.urd import URDCreate, URDResponse
from app.services.urd_service import URDService

router = APIRouter(
    prefix="/api/v1/auth/users",
    tags=["urd"],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)


@router.get("/{user_id}/urd", response_model=list[URDResponse])
async def list_urd(
    user_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[URDResponse]:
    return await URDService(conn).list(user_id)


@router.post(
    "/{user_id}/urd",
    response_model=URDResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def create_urd(
    user_id: uuid.UUID,
    payload: URDCreate,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> URDResponse:
    try:
        return await URDService(conn).create(
            user_id,
            payload,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            actor=str(caller.user_id),
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"El usuario ya tiene una URD en la dependencia {payload.depe_id}",
                "status": 409,
            },
        )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_reference",
                "detail": "user_id o rol_id no existen",
                "status": 400,
            },
        )


@router.delete(
    "/{user_id}/urd/{urd_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def delete_urd(
    user_id: uuid.UUID,
    urd_id: uuid.UUID,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    deleted = await URDService(conn).delete(
        user_id,
        urd_id,
        tenant_slug=request.headers.get("X-Tenant-Slug"),
        actor=str(caller.user_id),
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "URD no encontrada", "status": 404},
        )
