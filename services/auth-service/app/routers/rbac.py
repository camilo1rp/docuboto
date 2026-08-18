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
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.authz import Caller, get_caller, require_permission
from app.core.database import get_tenant_conn
from app.repositories.rbac_repository import RBACRepository
from app.schemas.rbac import (
    EffectivePermissions,
    GroupCreate,
    GroupPermissionItem,
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
    payload: UserCreate,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> UserResponse:
    service = RBACService(conn)
    try:
        row = await service.create_user(
            username=payload.username,
            email=payload.email,
            keycloak_sub=payload.keycloak_sub,
            is_root=payload.is_root,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            actor=str(caller.user_id),
            caller_is_root=caller.is_root,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict(f"Ya existe un usuario con el nombre «{payload.username}»")
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
        raise _not_found("No existe el usuario")
    service = RBACService(conn)
    perms = await service.resolve_effective_permissions(user_id)
    return EffectivePermissions(
        user_id=user_id,
        is_root=await repo.is_root(user_id),
        permissions=perms,
    )


# --- grupos -----------------------------------------------------------------
@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: GroupCreate,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> GroupResponse:
    service = RBACService(conn)
    try:
        row = await service.create_group(
            name=payload.name,
            description=payload.description,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            actor=str(caller.user_id),
        )
    except asyncpg.UniqueViolationError:
        raise _conflict(f"Ya existe un grupo con el nombre «{payload.name}»")
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
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[PermissionResponse]:
    """Catálogo de permisos. `USUA_PERM_ROOT` se OMITE para un llamante no-ROOT
    (no ofrecer en el selector de UI un permiso que el llamante no puede conceder,
    ver `RBACService.list_permissions`)."""
    service = RBACService(conn)
    rows = await service.list_permissions(caller_is_root=caller.is_root)
    return [PermissionResponse(**dict(r)) for r in rows]


# --- membresías y permisos de grupo ----------------------------------------
@router.post("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_201_CREATED)
async def add_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, str]:
    service = RBACService(conn)
    try:
        await service.add_member(
            user_id=user_id,
            group_id=group_id,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            actor=str(caller.user_id),
        )
    except asyncpg.ForeignKeyViolationError:
        raise _not_found("No existe el usuario o el grupo")
    return {"status": "ok"}


@router.delete("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    service = RBACService(conn)
    await service.remove_member(
        user_id=user_id,
        group_id=group_id,
        tenant_slug=request.headers.get("X-Tenant-Slug"),
        actor=str(caller.user_id),
    )


@router.get("/groups/{group_id}/members", response_model=list[UserResponse])
async def list_group_members(
    group_id: uuid.UUID,
    response: Response,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[UserResponse]:
    """Usuarios del grupo. Espejo de lectura de `POST/DELETE .../members/{user_id}`.

    404 si el grupo no existe en el tenant del llamante (search_path ya acota el
    schema: un group_id de OTRO tenant nunca aparece aquí — 404 indistinguible).
    """
    repo = RBACRepository(conn)
    if await repo.get_group(group_id) is None:
        raise _not_found("No existe el grupo")
    rows, total = await repo.list_group_members(group_id, limit=size, offset=(page - 1) * size)
    response.headers["X-Total-Count"] = str(total)
    return [UserResponse(**dict(r)) for r in rows]


@router.get("/groups/{group_id}/permissions", response_model=list[GroupPermissionItem])
async def list_group_permissions(
    group_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[GroupPermissionItem]:
    """Permisos asignados al grupo. Espejo de lectura de `PUT .../permissions/{permission_id}`."""
    repo = RBACRepository(conn)
    if await repo.get_group(group_id) is None:
        raise _not_found("No existe el grupo")
    rows = await repo.list_group_permissions(group_id)
    return [GroupPermissionItem(**dict(r)) for r in rows]


@router.put("/groups/{group_id}/permissions/{permission_id}", status_code=status.HTTP_200_OK)
async def set_group_permission(
    group_id: uuid.UUID,
    permission_id: uuid.UUID,
    payload: GroupPermissionSet,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, int | str]:
    service = RBACService(conn)
    try:
        await service.set_group_permission(
            group_id=group_id,
            permission_id=permission_id,
            crud=payload.crud,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            actor=str(caller.user_id),
            caller_is_root=caller.is_root,
        )
    except asyncpg.ForeignKeyViolationError:
        raise _not_found("No existe el grupo o el permiso")
    return {"status": "ok", "crud": payload.crud}
