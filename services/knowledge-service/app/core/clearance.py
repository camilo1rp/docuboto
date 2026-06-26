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

Gemelo del helper de document-service / archive-service (RF-SEG-08 / RF-BUS-04). El
nivel máximo de un usuario es el MAX de la matriz rol→nivel (``role_clearance``) sobre
sus grupos (``auth_memberships``); ``is_root`` accede a todo. Estas tablas las provee
auth-service pero VIVEN en el schema del tenant (ADR-002/ADR-012), por lo que
knowledge-service las resuelve desde su propia conexión (ADR-013: la BD autoriza).

La conexión debe tener el ``search_path`` ya fijado al schema del tenant (lo hace
``get_tenant_conn``). Política **fail-closed**: ante duda, PUBLICA (1).
"""

import uuid

import asyncpg

PUBLICA = 1
RESERVADA = 2
CLASIFICADA = 3  # nivel máximo (security_levels.code)


async def resolve_caller_clearance(
    conn: asyncpg.Connection, user_id: uuid.UUID | None
) -> int:
    """Devuelve el nivel de seguridad máximo (1..3) accesible por ``user_id``."""
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
