# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate de autorización resuelto en la BD del tenant (E06, ADR-013, RF-SEG-08).

Espejo de signature-service/app/core/clearance.py adaptado para notification-service
(ADR-021 Fase 5 Inc.2). notification-service no tenía previamente NINGUNA maquinaria
de clearance/RBAC: este módulo es el primer punto de entrada de ``require_permission``
y ``resolve_caller_clearance`` en este servicio.

El api-gateway inyecta:
  - ``X-User-Id``    → ``auth_users.id`` interno (UUID), resuelto por auth-service
                        ``/validate`` con lazy provisioning (NO es el keycloak_sub).
  - ``X-Tenant-Slug`` → slug del tenant (get_tenant_conn fija el search_path).

``require_permission``       verifica el permiso en las tablas RBAC del tenant (fail-closed).
``get_actor``                extrae el UUID del llamante desde X-User-Id (401 si ausente/inválido).
``resolve_caller_clearance`` resuelve el nivel de seguridad máximo del actor (1..3, fail-closed a 1).
El no-read-up de la DLQ ya NO vive aquí: lo evalúa la consulta (``orpycamcp_common.deadletter``, AREA-10-08).
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status
from orpycamcp_common.rbac import (  # noqa: F401 — re-exportados: los demás módulos del servicio importan de aquí
    CLASIFICADA,
    PUBLICA,
    check_permission,
    mensaje_denegacion,
    nivel_efectivo,
    resolve_caller_clearance,
)

from app.core.database import get_tenant_conn

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "notification-service"
logger = logging.getLogger(__name__)

# Security level constants (mirrors document-service, security_levels table).


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: raises 401/403 when caller lacks ``perm_name``.

    Reads actor UUID from X-User-Id header (injected by api-gateway after auth-service
    /validate resolved the internal id).  Raises 401 when the header is absent or not
    a valid UUID; 403 when permission is denied or user not found (fail-closed).

    ``min_crud`` (default ``1``, backward-compatible) requires a minimum level on the
    0..5 crud scale (RF-SEG-03); see :func:`check_permission`.

    Usage at endpoint level::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])

    Or at router level to protect all routes::

        router = APIRouter(dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])
    """

    async def _dep(
        actor: uuid.UUID = Depends(get_actor),
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        if not await check_permission(conn, actor, perm_name, min_crud):
            # Traza best-effort del acceso denegado (RF-SEG / MoReq). ``actor``
            # aquí SIEMPRE está identificado — ``get_actor`` ya devolvió 401 si
            # faltaba/era inválido X-User-Id — así que no hay caso anónimo que
            # excluir (a diferencia de document/archive/workflow).
            if _AUDIT_AVAILABLE:
                try:
                    await audit.append_denial(
                        conn,
                        tenant_slug=x_tenant_slug,
                        service=_SERVICE,
                        actor=str(actor),
                        perm_name=perm_name,
                        min_crud=min_crud,
                    )
                except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                    logger.warning("audit.append_denial failed", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": mensaje_denegacion(
                        perm_name, min_crud, await nivel_efectivo(conn, actor, perm_name)
                    ),
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
                "detail": "Falta la cabecera X-User-Id",
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
                "detail": "La cabecera X-User-Id debe ser un UUID válido (auth_users.id interno)",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
