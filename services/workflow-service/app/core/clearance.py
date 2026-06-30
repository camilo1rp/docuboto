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

Pieza de control de acceso de RF-SEG-08 / RF-BUS-04 (E08 §9, canal B "hoja de ruta
operativa"): la hoja de ruta de un radicado (`flow_events`, RF-FLU-02) la ve cualquier
usuario que pueda LEER el radicado, acotado por su clearance (no read-up), NO un permiso
de administrador. El nivel máximo de un usuario es el MAX de la matriz rol→nivel
(`role_clearance`) sobre sus grupos (`auth_memberships`); `is_root` accede a todo. Estas
tablas las provee auth-service pero VIVEN en el schema del tenant (ADR-002/012/013).

Política **fail-closed**: ante ausencia de identidad, usuario inexistente, tablas RBAC
no provisionadas o cualquier duda, se devuelve PUBLICA (1) — mínimo privilegio.
"""

import json
import uuid

import asyncpg
from fastapi import Depends, Header, HTTPException, status

from app.core.database import get_db_conn, tenant_schema

PUBLICA = 1
CLASIFICADA = 3  # nivel máximo (security_levels.code)


async def resolve_caller_clearance(
    conn: asyncpg.Connection, user_id: uuid.UUID | None
) -> int:
    """Devuelve el nivel de seguridad máximo (1..3) accesible por ``user_id``.

    La conexión debe tener ``search_path`` fijado al schema del tenant. Fail-closed a
    :data:`PUBLICA` en todos los casos dudosos.
    """
    if user_id is None:
        return PUBLICA
    try:
        row = await conn.fetchrow(
            """
            SELECT u.is_root,
                   (SELECT MAX(rc.max_level)
                      FROM auth_memberships m
                      JOIN role_clearance rc ON rc.group_id = m.group_id
                     WHERE m.user_id = u.id) AS max_level
            FROM auth_users u
            WHERE u.id = $1
            """,
            user_id,
        )
    except asyncpg.UndefinedTableError:
        return PUBLICA
    if row is None:
        return PUBLICA
    if row.get("is_root"):
        return CLASIFICADA
    max_level = row.get("max_level")
    return int(max_level) if max_level is not None else PUBLICA


async def check_permission(
    conn: asyncpg.Connection, user_id: uuid.UUID | None, perm_name: str
) -> bool:
    """Devuelve ``True`` si ``user_id`` tiene el permiso ``perm_name`` en el tenant.

    Gemelo de ``document-service``/``archive-service`` ``check_permission`` (D-06/D-08).
    La conexión debe tener ``search_path`` fijado al schema del tenant. Fail-closed a
    ``False`` en todos los casos dudosos: usuario ausente, tablas RBAC no provisionadas,
    ``user_id`` nulo.
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
        # Tablas RBAC del tenant ausentes (entorno aislado / no provisionado).
        return False
    return bool(allowed) if allowed is not None else False


def require_permission(perm_name: str):
    """FastAPI dependency factory: eleva 403 si el llamante carece de ``perm_name``.

    D-09: el workflow-service usa ``get_db_conn`` (sin search_path automático); esta
    dependencia fija el search_path al tenant antes de consultar las tablas RBAC.
    FastAPI cachea ``get_db_conn`` por request, así que la conexión que recibe esta
    dependencia es la misma que la del endpoint y el search_path es visible para ambos.
    Normaliza el slug igual que ``acquire_tenant`` (guiones → guiones bajos).

    Uso::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_X"))])
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
        # Sin tenant slug no se puede verificar el RBAC del tenant → deny EXPLÍCITO
        # (no depender de que la tabla no exista en el search_path heredado del pool).
        if not x_tenant_slug:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": "Se requiere X-Tenant-Slug para autorizar",
                    "status": 403,
                },
            )
        schema = tenant_schema(x_tenant_slug)  # valida + normaliza (fail-closed 400)
        await conn.execute(f'SET search_path TO "{schema}", public')
        if not await check_permission(conn, user_id, perm_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": f"Se requiere el permiso {perm_name}",
                    "status": 403,
                },
            )

    return _dep


async def assert_can_read_deadletter(
    conn: asyncpg.Connection,
    caller_clearance: int,
    row: asyncpg.Record,
) -> None:
    """No-read-up gate sobre un dead-letter (ADR-021 Fase 5, RF-SEG-08).

    Determina el nivel de seguridad del objeto referenciado por el evento aparcado
    de forma **fail-closed** sobre el nivel VIVO del radicado (no se confía solo en
    el nivel embebido en el sobre, que pudo quedar obsoleto). Si el llamante no
    alcanza ese nivel, responde **404 neutro** (no 403): no se revela ni la
    existencia ni el nivel del objeto.

    La conexión llega con ``search_path`` ya fijado al tenant (lo hace
    ``require_permission`` sobre la misma conexión cacheada por request).

    Reglas de determinación de nivel (fail-closed a CLASIFICADA=3 ante cualquier duda):
      - Sin identificadores de radicado en el sobre → CLASIFICADA.
      - Radicado no encontrado (o tabla ``radicados`` ausente) → CLASIFICADA.
      - En otro caso → ``max(nivel_vivo, nivel_embebido)`` (o ``nivel_vivo`` si no
        hay nivel embebido).
    """
    raw_payload = row["payload"]
    payload_data = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
    inner_payload = payload_data.get("payload", {}) if isinstance(payload_data, dict) else {}

    tracking_number = inner_payload.get("tracking_number")
    doc_id = inner_payload.get("id")
    nivel_embebido = inner_payload.get("nivel_nuevo")
    if nivel_embebido is None:
        nivel_embebido = inner_payload.get("nivel_seguridad")

    identifiers = [str(x) for x in (tracking_number, doc_id) if x is not None]

    if not identifiers:
        nivel_objeto = CLASIFICADA
    else:
        nivel_vivo: int | None = None
        try:
            for ident in identifiers:
                nivel_vivo = await conn.fetchval(
                    "SELECT nivel_seguridad FROM radicados "
                    "WHERE tracking_number = $1 OR id::text = $1 LIMIT 1",
                    ident,
                )
                if nivel_vivo is not None:
                    break
        except asyncpg.UndefinedTableError:
            nivel_vivo = None

        if nivel_vivo is None:
            nivel_objeto = CLASIFICADA
        elif nivel_embebido is not None:
            nivel_objeto = max(int(nivel_vivo), int(nivel_embebido))
        else:
            nivel_objeto = int(nivel_vivo)

    if caller_clearance < nivel_objeto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "Dead-letter event not found or not accessible",
                "status": 404,
            },
        )
