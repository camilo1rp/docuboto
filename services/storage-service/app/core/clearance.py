# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Verificación de permisos granulares del llamante (RF-SEG-08 / RF-BUS-04).

Gemelo de ``archive-service/app/core/clearance.py``. storage-service no modela
``nivel_seguridad`` sobre `files` (no hay concepto de clasificación por anexo hoy),
así que este módulo NO resuelve *clearance* — solo expone ``require_permission``,
suficiente para gatear la protección WORM del índice electrónico (E10 Increment A),
la operación de mayor privilegio de este servicio.

Como en archive-service, ``get_db_conn`` NO fija ``search_path`` automáticamente:
``require_permission`` lo fija explícitamente (vía ``tenant_schema``, validado)
antes de consultar las tablas RBAC del tenant. FastAPI cachea la dependencia
``get_db_conn`` por request, así que la conexión que ve ``require_permission``
es la MISMA que recibe el handler del endpoint.

Política **fail-closed**: ante ausencia de identidad, usuario inexistente, tablas
RBAC no provisionadas o cualquier duda, se deniega.
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

from app.core.database import get_db_conn, tenant_schema

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "storage-service"
logger = logging.getLogger(__name__)


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: eleva 403 si el llamante carece de ``perm_name``.

    Uso en el decorador del endpoint::

        @router.post("/...", dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))])
    """

    async def _dep(
        x_user_id: str | None = Header(default=None),
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_db_conn),
    ) -> None:
        try:
            user_id = uuid.UUID(x_user_id) if x_user_id else None
        except ValueError:
            user_id = None
        if not x_tenant_slug:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": "Se requiere X-Tenant-Slug para autorizar",
                    "status": 403,
                },
            )
        # Deriva y valida el schema (fail-closed 400 si el slug es inválido) — el
        # MISMO slug que luego usa el servicio para nombrar el bucket WORM, nunca
        # el del body de la petición.
        schema = tenant_schema(x_tenant_slug)
        await conn.execute(f'SET search_path TO "{schema}"')
        if not await check_permission(conn, user_id, perm_name, min_crud):
            # Traza best-effort del acceso denegado, solo para llamador IDENTIFICADO
            # (evita contaminar la cadena de hash con ruido de escaneo anónimo).
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
