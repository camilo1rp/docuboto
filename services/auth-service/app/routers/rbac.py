# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de administración RBAC (E08).

Todos exigen el permiso `USUA_PERM_ADMIN` del llamante, resuelto en la BD del tenant
por request (ADR-013); ROOT hace bypass. El api-gateway inyecta la identidad validada
(`X-User-Id`/`X-Tenant-Slug`).
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.core.authz import require_permission
from app.core.database import get_tenant_conn
from app.repositories.rbac_repository import RBACRepository
from app.schemas.rbac import (
    EffectivePermissions,
    GroupCreate,
    GroupPermissionSet,
    GroupResponse,
    PermissionResponse,
    UserCreate,
    UserPickItem,
    UserResponse,
)
from app.services.rbac_service import RBACService

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["rbac"],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)

# D-10: router hermano para selectores de UI. NO hereda el gate admin del router
# principal; solo exige PERM_RADI (cualquier operador de radicación). Devuelve una
# proyección mínima (id + username) sin PII administrativa, para el UserPicker (T-08c).
# Debe incluirse ANTES de `router` en main.py: la ruta estática `/users/pickable`
# tiene prioridad y no colisiona con `/users/{user_id}/...`.
pick_router = APIRouter(
    prefix="/api/v1/auth",
    tags=["rbac"],
    dependencies=[Depends(require_permission("PERM_RADI"))],
)


@pick_router.get("/users/pickable", response_model=list[UserPickItem])
async def list_pickable_users(
    size: int = Query(100, ge=1, le=500),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[UserPickItem]:
    """Lista mínima de usuarios activos (id + username) para selectores de UI.

    Gateado con `PERM_RADI` (no admin), a diferencia de `GET /users` (USUA_PERM_ADMIN).
    """
    repo = RBACRepository(conn)
    rows = await repo.list_pickable_users(limit=size)
    return [UserPickItem(**dict(r)) for r in rows]


def _conflict(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "conflict", "detail": detail, "status": 409},
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": detail, "status": 404},
    )


# --- usuarios ---------------------------------------------------------------
@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> UserResponse:
    repo = RBACRepository(conn)
    try:
        row = await repo.create_user(
            username=payload.username,
            email=payload.email,
            keycloak_sub=payload.keycloak_sub,
            is_root=payload.is_root,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict(f"User '{payload.username}' already exists")
    return UserResponse(**dict(row))


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    response: Response,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[UserResponse]:
    repo = RBACRepository(conn)
    rows, total = await repo.list_users(limit=size, offset=(page - 1) * size)
    response.headers["X-Total-Count"] = str(total)
    return [UserResponse(**dict(r)) for r in rows]


@router.get("/users/{user_id}/permissions", response_model=EffectivePermissions)
async def user_permissions(
    user_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> EffectivePermissions:
    repo = RBACRepository(conn)
    if await repo.get_user(user_id) is None:
        raise _not_found(f"User {user_id} not found")
    service = RBACService(repo)
    perms = await service.resolve_effective_permissions(user_id)
    return EffectivePermissions(
        user_id=user_id,
        is_root=await repo.is_root(user_id),
        permissions=perms,
    )


# --- grupos -----------------------------------------------------------------
@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: GroupCreate, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> GroupResponse:
    repo = RBACRepository(conn)
    try:
        row = await repo.create_group(name=payload.name, description=payload.description)
    except asyncpg.UniqueViolationError:
        raise _conflict(f"Group '{payload.name}' already exists")
    return GroupResponse(**dict(row))


@router.get("/groups", response_model=list[GroupResponse])
async def list_groups(
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[GroupResponse]:
    repo = RBACRepository(conn)
    return [GroupResponse(**dict(r)) for r in await repo.list_groups()]


# --- permisos ---------------------------------------------------------------
@router.get("/permissions", response_model=list[PermissionResponse])
async def list_permissions(
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[PermissionResponse]:
    repo = RBACRepository(conn)
    return [PermissionResponse(**dict(r)) for r in await repo.list_permissions()]


# --- membresías y permisos de grupo ----------------------------------------
@router.post("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_201_CREATED)
async def add_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, str]:
    repo = RBACRepository(conn)
    try:
        await repo.add_membership(user_id=user_id, group_id=group_id)
    except asyncpg.ForeignKeyViolationError:
        raise _not_found("User or group not found")
    return {"status": "ok"}


@router.delete("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = RBACRepository(conn)
    await repo.remove_membership(user_id=user_id, group_id=group_id)


@router.put("/groups/{group_id}/permissions/{permission_id}", status_code=status.HTTP_200_OK)
async def set_group_permission(
    group_id: uuid.UUID,
    permission_id: uuid.UUID,
    payload: GroupPermissionSet,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, int | str]:
    repo = RBACRepository(conn)
    try:
        await repo.set_group_permission(
            group_id=group_id, permission_id=permission_id, crud=payload.crud
        )
    except asyncpg.ForeignKeyViolationError:
        raise _not_found("Group or permission not found")
    return {"status": "ok", "crud": payload.crud}
