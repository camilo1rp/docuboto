# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate de autorización resuelto en la BD del tenant por request (E08, ADR-013).

Keycloak autentica; la BD del tenant autoriza. El api-gateway inyecta la identidad
ya validada (`X-User-Id` = sub de Keycloak, `X-Tenant-Slug`). Aquí se resuelve el
usuario local por `keycloak_sub`, se calculan sus permisos efectivos y se exige el
permiso requerido. ROOT (`USUA_PERM_ROOT`) hace bypass.
"""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.core.database import get_tenant_conn
from app.repositories.rbac_repository import RBACRepository
from app.services.rbac_service import RBACService, has_permission


@dataclass
class Caller:
    """Identidad autorizada del llamante (resuelta en la BD del tenant)."""

    user_id: UUID
    keycloak_sub: str
    is_root: bool
    permissions: dict[str, int]


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "forbidden", "detail": detail, "status": 403},
    )


async def get_caller(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> Caller:
    """Resuelve al llamante y sus permisos efectivos desde la BD del tenant.

    Dependencia estable (sobreescribible en tests). 401 si falta la identidad; 403 si
    el usuario no está aprovisionado en el tenant (lazy-provisioning: sin fila en
    auth_users → sin permisos). Comparte conexión con `get_tenant_conn` (FastAPI cachea
    la dependencia por request).
    """
    sub = request.headers.get("X-User-Id")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "detail": "X-User-Id header missing", "status": 401},
        )
    repo = RBACRepository(conn)
    user = await repo.get_user_by_keycloak_sub(sub)
    if user is None:
        raise _forbidden("Caller is not provisioned in this tenant")
    service = RBACService(repo)
    perms = await service.resolve_effective_permissions(user["id"])
    return Caller(
        user_id=user["id"],
        keycloak_sub=sub,
        is_root=bool(user["is_root"]),
        permissions=perms,
    )


def require_permission(name: str, min_crud: int = 1) -> Callable[..., Coroutine[Any, Any, Caller]]:
    """Dependencia FastAPI que exige ``name`` con al menos ``min_crud`` (ROOT bypass)."""

    async def dependency(caller: Caller = Depends(get_caller)) -> Caller:
        if not has_permission(caller.permissions, name, min_crud):
            raise _forbidden(f"Requires permission '{name}' (crud>={min_crud})")
        return caller

    return dependency
