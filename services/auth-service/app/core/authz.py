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
ya validada (`X-User-Id` = auth_users.id interno UUID, `X-Tenant-Slug`). Aquí se
resuelve el usuario local por `auth_users.id` (no por keycloak_sub — que era el
comportamiento anterior al fix RBAC), se calculan sus permisos efectivos y se exige
el permiso requerido. ROOT (`USUA_PERM_ROOT`) hace bypass.
"""

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.core.database import get_tenant_conn
from app.repositories.rbac_repository import RBACRepository
from app.services.rbac_service import RBACService, has_permission

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "auth-service"
logger = logging.getLogger(__name__)


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

    Dependencia estable (sobreescribible en tests). 401 si falta la identidad o si
    X-User-Id no es un UUID válido; 403 si el usuario no está aprovisionado en el
    tenant. Comparte la conexión con `get_tenant_conn` (FastAPI cachea la dependencia
    por request).

    X-User-Id es el ``auth_users.id`` interno (UUID), resuelto por auth-service
    ``/validate`` con lazy provisioning. Ya NO es el ``keycloak_sub``.
    """
    raw = request.headers.get("X-User-Id")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "detail": "Falta la cabecera X-User-Id", "status": 401},
        )
    try:
        user_id = UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "La cabecera X-User-Id debe ser un UUID válido (auth_users.id interno)",
                "status": 401,
            },
        )
    repo = RBACRepository(conn)
    user = await repo.get_user(user_id)   # looks up by auth_users.id, not keycloak_sub
    if user is None:
        # Traza best-effort (opcional, RF-SEG / MoReq): el llamante SÍ mandó un
        # X-User-Id identificable (UUID válido), simplemente no está aprovisionado
        # en este tenant — no es una sonda anónima, así que sí vale la pena dejar
        # rastro. ``perm_name`` no aplica aquí (no hay permiso concreto en juego);
        # se usa un marcador de object_ref distinto al de denegación por permiso,
        # y ``motivo`` explícito para que el asiento no se confunda con una
        # denegación por permiso insuficiente al filtrar por payload.motivo.
        if _AUDIT_AVAILABLE:
            try:
                await audit.append_denial(
                    conn,
                    tenant_slug=request.headers.get("X-Tenant-Slug"),
                    service=_SERVICE,
                    actor=str(user_id),
                    perm_name="__not_provisioned__",
                    min_crud=0,
                    motivo="usuario_no_aprovisionado",
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning("audit.append_denial failed", exc_info=True)
        raise _forbidden("El usuario no está aprovisionado en esta entidad")
    service = RBACService(conn)
    perms = await service.resolve_effective_permissions(user["id"])
    return Caller(
        user_id=user["id"],
        keycloak_sub=str(user.get("keycloak_sub") or ""),
        is_root=bool(user["is_root"]),
        permissions=perms,
    )


def require_permission(name: str, min_crud: int = 1) -> Callable[..., Coroutine[Any, Any, Caller]]:
    """Dependencia FastAPI que exige ``name`` con al menos ``min_crud`` (ROOT bypass)."""

    async def dependency(
        request: Request,
        caller: Caller = Depends(get_caller),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> Caller:
        if not has_permission(caller.permissions, name, min_crud):
            # Traza best-effort del acceso denegado (RF-SEG / MoReq). ``caller``
            # aquí SIEMPRE está identificado — ``get_caller`` ya devolvió 401/403
            # si faltaba/era inválido X-User-Id o si no estaba aprovisionado — así
            # que no hay caso anónimo que excluir.
            if _AUDIT_AVAILABLE:
                try:
                    await audit.append_denial(
                        conn,
                        tenant_slug=request.headers.get("X-Tenant-Slug"),
                        service=_SERVICE,
                        actor=str(caller.user_id),
                        perm_name=name,
                        min_crud=min_crud,
                    )
                except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                    logger.warning("audit.append_denial failed", exc_info=True)
            # El mensaje distingue las dos causas: faltar el permiso y tenerlo con
            # nivel insuficiente son dos problemas con dos soluciones distintas
            # (conceder vs. SUBIR el nivel de uno ya concedido). Aquí no hace falta
            # consulta extra: `caller.permissions` ya es {permiso: nivel}.
            nivel = caller.permissions.get(name)
            if nivel is None:
                raise _forbidden(f"Se requiere el permiso {name} (nivel {min_crud} o superior)")
            raise _forbidden(
                f"Tiene el permiso {name} con nivel {nivel}, y esta acción requiere "
                f"nivel {min_crud} o superior"
            )
        return caller

    return dependency
