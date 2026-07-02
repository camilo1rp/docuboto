# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate de autorización resuelto en la BD del tenant (E06, ADR-013, RF-SEG-08).

Espejo de document-service/app/core/clearance.py adaptado para signature-service.

El api-gateway inyecta:
  - ``X-User-Id``    → ``auth_users.id`` interno (UUID), resuelto por auth-service
                        ``/validate`` con lazy provisioning (NO es el keycloak_sub).
  - ``X-Tenant-Slug`` → slug del tenant (get_tenant_conn fija el search_path).

``require_permission``       verifica el permiso en las tablas RBAC del tenant (fail-closed).
``get_actor``                extrae el UUID del llamante desde X-User-Id (401 si ausente/inválido).
``resolve_caller_clearance`` resuelve el nivel de seguridad máximo del actor (1..3, fail-closed a 1).
``resolve_deadletter_level`` resuelve (sin lanzar) el nivel del objeto referenciado por un
                              dead-letter; usado tanto por el gate del detalle como por el
                              filtrado por-fila del listado (ADR-021 Fase 5, seguimiento).
``assert_can_read_deadletter`` gate 404-neutro del detalle, delega en ``resolve_deadletter_level``.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.core.database import get_tenant_conn

# Security level constants (mirrors document-service, security_levels table).
PUBLICA = 1
CLASIFICADA = 3  # maximum nivel (security_levels.code)


async def resolve_caller_clearance(
    conn: asyncpg.Connection, user_id: uuid.UUID | None
) -> int:
    """Returns the maximum security level (1..3) accessible by ``user_id``.

    The connection must have ``search_path`` set to the tenant schema (guaranteed by
    ``get_tenant_conn``).  Fail-closed to :data:`PUBLICA` on any doubt: unknown user,
    RBAC tables absent (UndefinedTableError), or ``user_id`` is None.
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
        # RBAC tables absent — tenant not provisioned yet.
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
) -> bool:
    """Returns True if user_id has perm_name in the tenant (fail-closed).

    Uses a single fetchval query that checks is_root (full bypass) or membership
    in a group that holds the permission with crud > 0.  Returns False on any
    doubt: None user_id, user not found, RBAC tables absent (UndefinedTableError).
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
        # RBAC tables absent — tenant not provisioned yet.
        return False
    # user not found → fetchval returns None → fail-closed
    return bool(allowed) if allowed is not None else False


def require_permission(perm_name: str):
    """FastAPI dependency factory: raises 401/403 when caller lacks ``perm_name``.

    Reads actor UUID from X-User-Id header (injected by api-gateway after auth-service
    /validate resolved the internal id).  Raises 401 when the header is absent or not
    a valid UUID; 403 when permission is denied or user not found (fail-closed).

    Usage at endpoint level::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_FIRMA"))])

    Or at router level to protect all routes::

        router = APIRouter(dependencies=[Depends(require_permission("PERM_FIRMA"))])
    """

    async def _dep(
        actor: uuid.UUID = Depends(get_actor),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        if not await check_permission(conn, actor, perm_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": f"Se requiere el permiso {perm_name}",
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
                "detail": "X-User-Id header missing",
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
                "detail": "X-User-Id must be a valid UUID (internal auth_users.id)",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


async def resolve_deadletter_level(conn: asyncpg.Connection, row: asyncpg.Record) -> int:
    """Resuelve el nivel de seguridad del objeto referenciado por un dead-letter.

    Single source of truth de la resolución de nivel: usada tanto por
    :func:`assert_can_read_deadletter` (gate del detalle) como por el filtrado
    por-fila del listado, de modo que ambos permanecen consistentes (una fila
    visible en el listado siempre es abrible en el detalle y viceversa).

    **Fail-closed a CLASIFICADA=3** ante cualquier duda (no se lanza; el llamante
    decide qué hacer con el nivel devuelto):
      - Sin identificadores de radicado en el sobre → CLASIFICADA.
      - Radicado no encontrado (o tabla ``radicados`` ausente) → CLASIFICADA.
      - En otro caso → ``max(nivel_vivo, nivel_embebido)`` (o ``nivel_vivo`` si no
        hay nivel embebido). El nivel VIVO del radicado siempre se consulta (no
        basta el nivel embebido en el sobre, que pudo quedar obsoleto: un
        radicado reclasificado hacia arriba tras el dead-letter debe ocultarse
        de inmediato).
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
        # No hay manera de resolver el radicado → fail-closed.
        return CLASIFICADA

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
        # Radicado no hallado → nunca degradar a público.
        return CLASIFICADA
    if nivel_embebido is not None:
        return max(int(nivel_vivo), int(nivel_embebido))
    return int(nivel_vivo)


async def assert_can_read_deadletter(
    conn: asyncpg.Connection,
    caller_clearance: int,
    row: asyncpg.Record,
) -> None:
    """No-read-up gate sobre un dead-letter (ADR-021 Fase 5, RF-SEG-08).

    Delega la determinación de nivel en :func:`resolve_deadletter_level` (misma
    resolución que usa el filtrado del listado). Si el llamante no alcanza ese
    nivel, responde **404 neutro** (no 403): no se revela ni la existencia ni el
    nivel del objeto.
    """
    nivel_objeto = await resolve_deadletter_level(conn, row)
    if caller_clearance < nivel_objeto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "Dead-letter event not found or not accessible",
                "status": 404,
            },
        )
