# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Resolución del *clearance* (nivel de seguridad máximo accesible) del llamante.

Pieza de control de acceso de RF-SEG-08 / RF-BUS-04. El nivel máximo de un usuario
es el MAX de la matriz rol→nivel (`role_clearance`) sobre los grupos a los que
pertenece (`auth_memberships`); un usuario `is_root` accede a todo. Estas tablas las
provee auth-service pero VIVEN en el schema del tenant (ADR-002/ADR-012, una BD con
un schema por tenant), por lo que document-service las resuelve desde su propia
conexión ya posicionada en el tenant (ADR-013: la autorización la resuelve la BD por
request, no un claim del token).

Política **fail-closed**: ante ausencia de identidad, usuario inexistente, tablas RBAC
no provisionadas o cualquier duda, se devuelve PUBLICA (1) — el mínimo privilegio.
Igual para ``check_permission``: ante cualquier duda devuelve ``False``.
"""

import logging
import uuid

import asyncpg
from fastapi import Depends, Header, HTTPException, status
from orpycamcp_common.rbac import (  # noqa: F401 — re-exportados: los demás módulos del servicio importan de aquí
    CLASIFICADA,
    PUBLICA,
    check_permission,
    make_require_any_permission,
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

_SERVICE = "document-service"
logger = logging.getLogger(__name__)



# Gate any-of (basta UNO de los permisos): la lógica vive en orpycamcp_common.rbac.
require_any_permission = make_require_any_permission(
    service=_SERVICE, get_conn=get_tenant_conn
)


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: eleva 403 si el llamante carece de ``perm_name``.

    ``min_crud`` (default ``1``, retrocompatible) exige un nivel mínimo en la escala
    crud 0..5 (RF-SEG-03); ver :func:`check_permission`.

    Uso en el decorador del endpoint::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_X"))])

    La conexión se comparte con el endpoint (FastAPI cachea ``get_tenant_conn``
    por request), así que no se abre una segunda conexión.
    """

    async def _dep(
        x_user_id: str | None = Header(default=None),
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        try:
            user_id = uuid.UUID(x_user_id) if x_user_id else None
        except ValueError:
            user_id = None
        if not await check_permission(conn, user_id, perm_name, min_crud):
            # Traza best-effort del acceso denegado (RF-SEG / MoReq). Solo para un
            # usuario IDENTIFICADO (user_id no nulo); las sondas anónimas (sin
            # X-User-Id válido) no se auditan para no contaminar la cadena de hash
            # con ruido de escaneo (el rate-limiter ya las mitiga).
            if _AUDIT_AVAILABLE and user_id is not None:
                try:
                    await audit.append_denial(
                        conn,
                        tenant_slug=x_tenant_slug,
                        service=_SERVICE,
                        actor=str(user_id),
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
                        perm_name, min_crud, await nivel_efectivo(conn, user_id, perm_name)
                    ),
                    "status": 403,
                },
            )

    return _dep
