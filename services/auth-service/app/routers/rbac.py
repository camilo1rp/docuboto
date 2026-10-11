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
por request (ADR-013); ROOT hace bypass. Las LECTURAS bastan con el nivel 1; toda
ESCRITURA (usuarios, grupos, membresías, permisos de grupo) exige `min_crud=3`: con
el gate de router a nivel 1, quien tenía el permiso solo para leer la auditoría
podía crear usuarios, entrar en grupos y concederse permisos (AREA-01-01). El api-gateway inyecta la identidad validada
(`X-User-Id`/`X-Tenant-Slug`).
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.authz import Caller, get_caller, require_any_permission, require_permission
from app.core.database import get_tenant_conn
from app.repositories.clearance_repository import MAX_LEVEL, ClearanceRepository
from app.repositories.rbac_repository import RBACRepository
from app.schemas.rbac import (
    EffectivePermissions,
    GroupCreate,
    GroupPermissionItem,
    GroupPermissionSet,
    GroupResponse,
    GroupUpdate,
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
# principal; exige PERM_RADI **o** USUA_PERM_ADMIN (any-of): el operador de
# radicación lo necesita para elegir destinatario y el administrador de /admin
# (flujos, grupos) para elegir usuario aunque no radique. No se gana exposición:
# el administrador ya lista TODOS los usuarios con `GET /users`, con más datos.
# Devuelve una proyección mínima (id + username) sin PII administrativa, para el UserPicker (T-08c).
# Debe incluirse ANTES de `router` en main.py: la ruta estática `/users/pickable`
# tiene prioridad y no colisiona con `/users/{user_id}/...`.
pick_router = APIRouter(
    prefix="/api/v1/auth",
    tags=["rbac"],
    dependencies=[Depends(require_any_permission("PERM_RADI", "USUA_PERM_ADMIN"))],
)


@pick_router.get("/users/pickable", response_model=list[UserPickItem])
async def list_pickable_users(
    response: Response,
    size: int = Query(100, ge=1, le=500),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[UserPickItem]:
    """Lista mínima de usuarios activos (id + username) para selectores de UI.

    Gateado con `PERM_RADI` o `USUA_PERM_ADMIN` (any-of), a diferencia de `GET /users` (USUA_PERM_ADMIN).

    Emite `X-Total-Count` con **cuántos usuarios activos hay**, no cuántos caben
    en la respuesta. Sin esa cabecera, el `LIMIT` recortaba sin que nada lo
    delatara: quien no entrara en la ventana no existía para quien busca. La
    convención es la misma que el resto de listados del proyecto, y aquí importa
    más que en otros porque este endpoint no pagina — la única salida del
    llamante es saber que está viendo un recorte.
    """
    repo = RBACRepository(conn)
    rows, total = await repo.list_pickable_users(limit=size)
    response.headers["X-Total-Count"] = str(total)
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
@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
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
@router.post(
    "/groups",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
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


@router.patch(
    "/groups/{group_id}",
    response_model=GroupResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def update_group(
    group_id: uuid.UUID,
    payload: GroupUpdate,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> GroupResponse:
    fields = {k: getattr(payload, k) for k in payload.model_fields_set}
    if fields.get("name", "x") is None:
        fields.pop("name")  # el nombre es NOT NULL: `null` equivale a «no tocar»
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_patch", "detail": "Nada que actualizar", "status": 400},
        )
    try:
        row = await RBACService(conn).update_group(
            group_id=group_id,
            fields=fields,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            actor=str(caller.user_id),
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Ya existe un grupo con ese nombre")
    if row is None:
        raise _not_found("No existe el grupo")
    return GroupResponse(**dict(row))


@router.delete(
    "/groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def delete_group(
    group_id: uuid.UUID,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    """Elimina un grupo vacío. 409 si tiene miembros o URD (hay que retirarlos
    antes: borrar el grupo les quitaría permisos y clearance sin que nadie lo decida)."""
    outcome = await RBACService(conn).delete_group(
        group_id=group_id,
        tenant_slug=request.headers.get("X-Tenant-Slug"),
        actor=str(caller.user_id),
    )
    if outcome == "missing":
        raise _not_found("No existe el grupo")
    if outcome == "in_use":
        raise _conflict("El grupo tiene miembros o usuarios con URD: retírelos antes de eliminarlo")


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
@router.post(
    "/groups/{group_id}/members/{user_id}",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
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
            caller_is_root=caller.is_root,
            caller_level=(
                MAX_LEVEL
                if caller.is_root
                else await ClearanceRepository(conn).caller_max_level(caller.user_id)
            ),
        )
    except asyncpg.ForeignKeyViolationError:
        raise _not_found("No existe el usuario o el grupo")
    return {"status": "ok"}


@router.delete(
    "/groups/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
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


@router.put(
    "/groups/{group_id}/permissions/{permission_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
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
