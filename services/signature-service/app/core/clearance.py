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
from collections.abc import Sequence

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
    min_crud: int = 1,
) -> bool:
    """Returns True if user_id has perm_name in the tenant (fail-closed).

    Uses a single fetchval query that checks is_root (full bypass) or membership
    in a group that holds the permission with crud >= min_crud.  Returns False on
    any doubt: None user_id, user not found, RBAC tables absent (UndefinedTableError).

    ``min_crud`` is the minimum level required on ``auth_group_permissions.crud``
    (0..5 scale, RF-SEG-03). The default ``1`` (Leer) is backward-compatible with
    the previous ``crud > 0`` behavior, since ``crud`` is never negative.
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
        # RBAC tables absent — tenant not provisioned yet.
        return False
    # user not found → fetchval returns None → fail-closed
    return bool(allowed) if allowed is not None else False


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: raises 401/403 when caller lacks ``perm_name``.

    Reads actor UUID from X-User-Id header (injected by api-gateway after auth-service
    /validate resolved the internal id).  Raises 401 when the header is absent or not
    a valid UUID; 403 when permission is denied or user not found (fail-closed).

    ``min_crud`` (default ``1``, backward-compatible) requires a minimum level on the
    0..5 crud scale (RF-SEG-03); see :func:`check_permission`.

    Usage at endpoint level::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_FIRMA"))])

    Or at router level to protect all routes::

        router = APIRouter(dependencies=[Depends(require_permission("PERM_FIRMA"))])
    """

    async def _dep(
        actor: uuid.UUID = Depends(get_actor),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        if not await check_permission(conn, actor, perm_name, min_crud):
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


def _extract_ref(row: asyncpg.Record) -> tuple[list[str], int | None]:
    """Parsea el sobre de un dead-letter y extrae los identificadores del
    radicado referenciado (``tracking_number`` primero, luego ``id``) y el
    nivel embebido en el evento (``nivel_nuevo`` o, en su defecto,
    ``nivel_seguridad``).

    Factorizado fuera de :func:`resolve_deadletter_level` para que también lo
    reutilice :func:`resolve_deadletter_levels` (resolución en lote del
    listado, ADR-021 seguimiento Baja [2]) — una sola implementación evita que
    las dos rutas de resolución (per-row y en lote) diverjan con el tiempo.

    Puede lanzar (``json.loads`` sobre un payload corrupto): el llamante
    decide si eso implica fail-closed o debe propagarse.
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
    return identifiers, nivel_embebido


async def resolve_deadletter_level(conn: asyncpg.Connection, row: asyncpg.Record) -> int:
    """Resuelve el nivel de seguridad del objeto referenciado por un dead-letter.

    Single source of truth de la resolución de nivel para el GATE DEL DETALLE
    (:func:`assert_can_read_deadletter`, y por tanto replay/discard). El
    listado usa en cambio :func:`resolve_deadletter_levels` (resolución en
    lote, una sola consulta) para evitar el N+1 de invocar esta función fila a
    fila; ambas comparten el parseo (:func:`_extract_ref`) y el mismo criterio
    fail-closed, de modo que una fila visible en el listado siempre es abrible
    en el detalle y viceversa.

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
    identifiers, nivel_embebido = _extract_ref(row)

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


async def resolve_deadletter_levels(
    conn: asyncpg.Connection,
    rows: Sequence[asyncpg.Record],
) -> dict[str, int]:
    """Resuelve el nivel de seguridad de VARIAS filas de dead-letter EN LOTE
    (ADR-021 seguimiento Baja [2]) — una sola consulta a ``radicados`` en vez
    de N, usada exclusivamente por el listado (:func:`resolve_deadletter_level`
    per-row sigue siendo la fuente única de verdad del gate del detalle).

    Devuelve ``{event_id: nivel}``. NUNCA lanza: cualquier fila cuyo nivel no
    se pueda determinar con certeza queda fail-closed a CLASIFICADA, exactamente
    como haría el bucle ``try/except`` por-fila que este helper reemplaza en el
    router. Contrato de equivalencia: para toda fila ``r``, el resultado aquí
    para ``r["event_id"]`` es idéntico al que produciría llamar
    ``resolve_deadletter_level(conn, r)`` y, ante cualquier excepción, tratarlo
    como CLASIFICADA.
    """
    # Fase 1 — parseo por fila: identificadores + nivel embebido. Un payload
    # corrupto (json.loads lanza) o sin identificador queda CLASIFICADA de una
    # vez, sin llegar a la consulta en lote.
    parsed: dict[str, tuple[list[str], int | None]] = {}
    levels: dict[str, int] = {}
    all_idents: set[str] = set()
    for row in rows:
        event_id = row["event_id"]
        try:
            identifiers, nivel_embebido = _extract_ref(row)
        except Exception:  # noqa: BLE001 — payload corrupto → fail-closed
            levels[event_id] = CLASIFICADA
            continue
        if not identifiers:
            levels[event_id] = CLASIFICADA
            continue
        parsed[event_id] = (identifiers, nivel_embebido)
        all_idents.update(identifiers)

    # Fase 2 — UNA sola consulta con el nivel VIVO de todos los identificadores
    # recolectados (el N+1 que este helper elimina). ``UndefinedTableError``
    # (tabla ``radicados`` ausente, p.ej. notification-service) deja el mapa
    # vacío: toda fila parseable cae entonces a no-hallado → CLASIFICADA en la
    # fase 3, igual que el per-row.
    level_by_ident: dict[str, int] = {}
    if all_idents:
        try:
            recs = await conn.fetch(
                "SELECT nivel_seguridad, tracking_number, id FROM radicados "
                "WHERE tracking_number = ANY($1::text[]) OR id::text = ANY($1::text[])",
                list(all_idents),
            )
        except asyncpg.UndefinedTableError:
            recs = []
        for rec in recs:
            nivel = rec["nivel_seguridad"]
            if rec["tracking_number"] is not None:
                level_by_ident[str(rec["tracking_number"])] = nivel
            level_by_ident[str(rec["id"])] = nivel

    # Fase 3 — resolver cada fila parseable con el mismo criterio fail-closed
    # que resolve_deadletter_level: el nivel embebido NUNCA rescata un
    # no-hallado, solo eleva un nivel ya hallado.
    for event_id, (identifiers, nivel_embebido) in parsed.items():
        try:
            nivel_vivo: int | None = None
            for ident in identifiers:
                if ident in level_by_ident:
                    nivel_vivo = level_by_ident[ident]
                    break
            if nivel_vivo is None:
                levels[event_id] = CLASIFICADA
            elif nivel_embebido is not None:
                levels[event_id] = max(int(nivel_vivo), int(nivel_embebido))
            else:
                levels[event_id] = int(nivel_vivo)
        except Exception:  # noqa: BLE001 — fail-closed por fila
            levels[event_id] = CLASIFICADA

    return levels


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
