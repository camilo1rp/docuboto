# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate de autorización RBAC resuelto en la BD del tenant por request (E08/E14).

Keycloak autentica; la BD del tenant autoriza. El api-gateway inyecta la identidad
ya validada (`X-User-Id` = auth_users.id interno UUID, `X-Tenant-Slug`) — NUNCA
media el acceso por sí mismo (solo enruta prefijo→upstream, ver
`api-gateway/app/routers/proxy.py`). Las tablas `auth_users`/`auth_memberships`/
`auth_group_permissions`/`auth_permissions` las provisiona auth-service pero VIVEN
en el schema del tenant (ADR-002/ADR-012), así que tenant-service las resuelve con
su propia conexión ya posicionada en el tenant por `get_tenant_conn` — mismo patrón
que `document-service/app/core/clearance.py` y `archive-service`.

Política fail-closed: ante cualquier duda (usuario ausente, tablas RBAC no
provisionadas, header inválido) se deniega.
"""

import logging
import uuid

import asyncpg
from fastapi import Depends, Header, HTTPException, status
from orpycamcp_common.rbac import (  # noqa: F401 — re-exportados: los demás módulos del servicio importan de aquí
    check_permission,
    mensaje_denegacion,
    nivel_efectivo,
)

from app.core.database import get_tenant_conn

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "tenant-service"
logger = logging.getLogger(__name__)


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: eleva 403 si el llamante carece de ``perm_name``.

    Uso a nivel de router::

        @router.post("/...", dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))])

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
            # usuario IDENTIFICADO (X-User-Id válido); las sondas anónimas no se
            # auditan para no contaminar la cadena de hash con ruido de escaneo.
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
