# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate de autorización resuelto en la BD del tenant (E06, ADR-013).

Espejo de document-service/app/core/clearance.py adaptado para signature-service.

El api-gateway inyecta:
  - ``X-User-Id``    → ``auth_users.id`` interno (UUID), resuelto por auth-service
                        ``/validate`` con lazy provisioning (NO es el keycloak_sub).
  - ``X-Tenant-Slug`` → slug del tenant (get_tenant_conn fija el search_path).

``require_permission`` verifica el permiso en las tablas RBAC del tenant (fail-closed).
``get_actor``         extrae el UUID del llamante desde X-User-Id (401 si ausente/inválido).
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.core.database import get_tenant_conn


async def check_permission(
    conn: asyncpg.Connection,
    user_id: uuid.UUID | None,
    perm_name: str,
) -> bool:
    """Returns True if user_id has perm_name in the tenant (fail-closed).

    Uses a single fetchval query that checks is_root (full bypass) or membership
    in a group that holds the permission with crud > 0.  Returns False on any
    doubt: None user_id, user not found, RBAC tables absent (UndefinedTableError).
    """
    if user_id is None:
        return False
    try:
        allowed = await conn.fetchval(
            """
            SELECT (
                u.is_root
                OR EXISTS (
                    SELECT 1
                    FROM auth_memberships m
                    JOIN auth_group_permissions gp ON gp.group_id = m.group_id
                    JOIN auth_permissions p ON p.id = gp.permission_id
                    WHERE m.user_id = u.id
                      AND p.nombre = $2
                      AND gp.crud > 0
                )
            )
            FROM auth_users u
            WHERE u.id = $1
            """,
            user_id,
            perm_name,
        )
    except asyncpg.UndefinedTableError:
        # RBAC tables absent — tenant not provisioned yet.
        return False
    # user not found → fetchval returns None → fail-closed
    return bool(allowed) if allowed is not None else False


def require_permission(perm_name: str):
    """FastAPI dependency factory: raises 401/403 when caller lacks ``perm_name``.

    Reads actor UUID from X-User-Id header (injected by api-gateway after auth-service
    /validate resolved the internal id).  Raises 401 when the header is absent or not
    a valid UUID; 403 when permission is denied or user not found (fail-closed).

    Usage at endpoint level::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_FIRMA"))])

    Or at router level to protect all routes::

        router = APIRouter(dependencies=[Depends(require_permission("PERM_FIRMA"))])
    """

    async def _dep(
        actor: uuid.UUID = Depends(get_actor),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        if not await check_permission(conn, actor, perm_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": f"Se requiere el permiso {perm_name}",
                    "status": 403,
                },
            )

    return _dep


async def get_actor(request: Request) -> uuid.UUID:
    """Extracts the caller's internal UUID from X-User-Id header.

    Raises 401 when the header is missing or not a valid UUID.  Never touches the
    database — identity was already validated by auth-service /validate.
    """
    raw = request.headers.get("X-User-Id")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "X-User-Id header missing",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "X-User-Id must be a valid UUID (internal auth_users.id)",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
