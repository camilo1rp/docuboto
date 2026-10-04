# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Resolución de permisos y *clearance* del llamante, UNA sola vez (AREA-01-17).

`check_permission`, `resolve_caller_clearance`, `nivel_efectivo` y
`mensaje_denegacion` estaban copiadas byte a byte en nueve servicios (cada uno con
su `core/clearance.py` o `core/authz.py`). Un gemelo que diverge en la política
fail-closed de una sola copia es un hueco de autorización que ningún test del
servicio sano ve; por eso la definición vive aquí y los servicios la importan. Un
guardián (`shared/tests/test_el_gate_se_define_una_vez.py`) falla si reaparece una
definición fuera de esta librería.

Autocreación y caché (ADR-013): el usuario se aprovisiona en `/validate`
(`upsert_user_by_keycloak_sub`); la resolución NO se cachea a propósito (un permiso
revocado debe surtir efecto en la siguiente petición). La conexión llega con el
`search_path` ya fijado al schema del tenant.

Política **fail-closed**: ante ausencia de identidad, usuario inexistente, tablas
RBAC no provisionadas o cualquier duda, el nivel es PUBLICA (1) y el permiso `False`.
"""

import uuid

import asyncpg

PUBLICA = 1
CLASIFICADA = 3  # nivel máximo (security_levels.code)


async def resolve_caller_clearance(
    conn: asyncpg.Connection, user_id: uuid.UUID | None
) -> int:
    """Devuelve el nivel de seguridad máximo (1..3) accesible por ``user_id``.

    La conexión debe tener ``search_path`` fijado al schema del tenant (lo garantiza
    ``get_tenant_conn``). Fail-closed a :data:`PUBLICA` en todos los casos dudosos.
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
        # Tablas RBAC del tenant ausentes (entorno aislado / no provisionado).
        return PUBLICA
    if row is None:
        return PUBLICA
    if row.get("is_root"):
        return CLASIFICADA
    max_level = row.get("max_level")
    return int(max_level) if max_level is not None else PUBLICA


async def check_permission(
    conn: asyncpg.Connection,
    user_id: uuid.UUID | None,
    perm_name: str,
    min_crud: int = 1,
) -> bool:
    """Devuelve ``True`` si ``user_id`` tiene el permiso ``perm_name`` en el tenant.

    La conexión debe tener ``search_path`` fijado al schema del tenant (lo garantiza
    ``get_tenant_conn``). Fail-closed a ``False`` en todos los casos dudosos: usuario
    ausente, tablas RBAC no provisionadas, ``user_id`` nulo.

    ``min_crud`` es el nivel mínimo exigido en ``auth_group_permissions.crud`` (escala
    0..5, RF-SEG-03). El default ``1`` (Leer) es retrocompatible con el comportamiento
    previo ``crud > 0``, ya que ``crud`` nunca es negativo. Operaciones de disposición
    (cerrar/transferir expediente, retención TRD, etc.) elevan este umbral.

    Usa ``fetchval`` (query booleana) para no interferir con el pool de ``fetchrow``
    de la lógica de negocio en tests unitarios.
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
    # user not found → fetchval devuelve None → fail-closed
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
