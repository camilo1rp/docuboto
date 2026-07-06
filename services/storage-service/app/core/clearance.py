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

from app.core.database import get_db_conn, tenant_schema

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "storage-service"
logger = logging.getLogger(__name__)


async def check_permission(
    conn: asyncpg.Connection,
    user_id: uuid.UUID | None,
    perm_name: str,
    min_crud: int = 1,
) -> bool:
    """Devuelve ``True`` si ``user_id`` tiene el permiso ``perm_name`` en el tenant.

    La conexión debe tener ``search_path`` fijado al schema del tenant. Fail-closed
    a ``False`` en todos los casos dudosos: usuario ausente, tablas RBAC no
    provisionadas, ``user_id`` nulo.

    ``min_crud`` es el nivel mínimo exigido en ``auth_group_permissions.crud``
    (escala 0..5, RF-SEG-03). Las operaciones de protección WORM son de
    disposición/alto-privilegio (irreversibles) y exigen ``min_crud=3``, en línea
    con el umbral que usan archive-service/document-service para disposición.
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
                      AND gp.crud >= $3
                )
            )
            FROM auth_users u
            WHERE u.id = $1
            """,
            user_id,
            perm_name,
            min_crud,
        )
    except asyncpg.UndefinedTableError:
        # Tablas RBAC del tenant ausentes (entorno aislado / no provisionado).
        return False
    return bool(allowed) if allowed is not None else False


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
        await conn.execute(f'SET search_path TO "{schema}", public')
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
                    "detail": f"Se requiere el permiso {perm_name}",
                    "status": 403,
                },
            )

    return _dep
