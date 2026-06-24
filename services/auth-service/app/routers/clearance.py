# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Clasificación de seguridad y verificación de acceso (E08, RF-SEG-08).

- Catálogo de niveles (lectura, autenticado).
- Asignar el nivel máximo de un grupo (admin).
- El llamante consulta su clearance y verifica acceso a un nivel (mínimo
  privilegio): puede acceder a un documento de nivel L si su max_level ≥ L;
  ROOT accede a todo.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.authz import Caller, get_caller, require_permission
from app.core.database import get_tenant_conn
from app.repositories.clearance_repository import ClearanceRepository
from app.schemas.security import (
    CallerClearance,
    ClearanceCheck,
    ClearanceSet,
    SecurityLevel,
)

router = APIRouter(prefix="/api/v1/auth", tags=["security"])

_MAX_LEVEL = 3  # CLASIFICADA


async def _effective_max_level(caller: Caller, repo: ClearanceRepository) -> int:
    return _MAX_LEVEL if caller.is_root else await repo.caller_max_level(caller.user_id)


@router.get("/security-levels", response_model=list[SecurityLevel])
async def list_security_levels(
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[SecurityLevel]:
    rows = await ClearanceRepository(conn).list_levels()
    return [SecurityLevel(code=r["code"], nombre=r["nombre"]) for r in rows]


@router.put(
    "/groups/{group_id}/clearance",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def set_group_clearance(
    group_id: uuid.UUID,
    payload: ClearanceSet,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = ClearanceRepository(conn)
    if not await repo.level_exists(payload.max_level):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_level", "detail": "Nivel inexistente", "status": 400},
        )
    try:
        await repo.set_group_clearance(group_id, payload.max_level)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Grupo no encontrado", "status": 404},
        )


@router.get("/clearance", response_model=CallerClearance)
async def my_clearance(
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> CallerClearance:
    max_level = await _effective_max_level(caller, ClearanceRepository(conn))
    return CallerClearance(
        user_id=str(caller.user_id),
        max_level=max_level,
        accessible_levels=list(range(1, max_level + 1)),
    )


@router.get("/clearance/check", response_model=ClearanceCheck)
async def check_clearance(
    level: int = Query(..., ge=1, le=3),
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ClearanceCheck:
    max_level = await _effective_max_level(caller, ClearanceRepository(conn))
    return ClearanceCheck(level=level, max_level=max_level, allowed=max_level >= level)
