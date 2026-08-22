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

from app.core.database import get_tenant_conn

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "tenant-service"
logger = logging.getLogger(__name__)


async def check_permission(
    conn: asyncpg.Connection,
    user_id: uuid.UUID | None,
    perm_name: str,
    min_crud: int = 1,
) -> bool:
    """Devuelve ``True`` si ``user_id`` tiene ``perm_name`` en el tenant (ROOT bypass).

    La conexión debe tener ``search_path`` fijado al schema del tenant (lo
    garantiza ``get_tenant_conn``). Fail-closed a ``False`` en todos los casos
    dudosos: usuario ausente, tablas RBAC no provisionadas, ``user_id`` nulo.

    ``min_crud`` es el nivel mínimo exigido en ``auth_group_permissions.crud``
    (escala 0..5, RF-SEG-03); el default ``1`` (Leer) es el umbral habitual para
    gates de escritura administrativa en este servicio.
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


async def nivel_efectivo(
    conn: asyncpg.Connection, user_id: uuid.UUID | None, perm_name: str
) -> int | None:
    """Nivel CRUD que el llamante tiene HOY sobre ``perm_name``, o ``None`` si no lo tiene.

    Se usa SOLO en la rama de denegación, para poder decir la verdad. El mensaje
    anterior era ``"Se requiere el permiso X"`` tanto si al usuario le faltaba el
    permiso como si lo tenía con nivel insuficiente — y son dos problemas con dos
    soluciones distintas: en el primer caso hay que concederlo, en el segundo hay
    que **subir el nivel** de uno que ya está concedido. Un funcionario al que se
    le dice que le falta un permiso que sí tiene manda a su administrador a buscar
    una concesión que ya existe.

    Un ROOT no llega aquí (pasa el gate), así que no hay que contemplarlo.
    Fail-closed a ``None`` ante cualquier duda: es el mensaje más conservador.
    """
    if user_id is None:
        return None
    try:
        nivel = await conn.fetchval(
            """
            SELECT max(gp.crud)
            FROM auth_memberships m
            JOIN auth_group_permissions gp ON gp.group_id = m.group_id
            JOIN auth_permissions p ON p.id = gp.permission_id
            WHERE m.user_id = $1 AND p.nombre = $2
            """,
            user_id,
            perm_name,
        )
    except Exception:  # noqa: BLE001 — el mensaje nunca puede tumbar la respuesta.
        return None
    # Se exige un entero de verdad: `bool` es subclase de `int` en Python, y sin
    # este filtro un `False` se cuela y la frase acaba diciendo «con nivel False»
    # a un funcionario. Cualquier otra cosa cae al mensaje conservador.
    if isinstance(nivel, bool) or not isinstance(nivel, int):
        return None
    return nivel


def mensaje_denegacion(perm_name: str, min_crud: int, nivel: int | None) -> str:
    """Texto del 403 que distingue «no lo tiene» de «lo tiene corto de nivel»."""
    if nivel is None:
        return f"Se requiere el permiso {perm_name} (nivel {min_crud} o superior)"
    return (
        f"Tiene el permiso {perm_name} con nivel {nivel}, y esta acción requiere "
        f"nivel {min_crud} o superior"
    )


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
