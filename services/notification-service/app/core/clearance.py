# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate de autorización resuelto en la BD del tenant (E06, ADR-013, RF-SEG-08).

Espejo de signature-service/app/core/clearance.py adaptado para notification-service
(ADR-021 Fase 5 Inc.2). notification-service no tenía previamente NINGUNA maquinaria
de clearance/RBAC: este módulo es el primer punto de entrada de ``require_permission``
y ``resolve_caller_clearance`` en este servicio.

El api-gateway inyecta:
  - ``X-User-Id``    → ``auth_users.id`` interno (UUID), resuelto por auth-service
                        ``/validate`` con lazy provisioning (NO es el keycloak_sub).
  - ``X-Tenant-Slug`` → slug del tenant (get_tenant_conn fija el search_path).

``require_permission``       verifica el permiso en las tablas RBAC del tenant (fail-closed).
``get_actor``                extrae el UUID del llamante desde X-User-Id (401 si ausente/inválido).
``resolve_caller_clearance`` resuelve el nivel de seguridad máximo del actor (1..3, fail-closed a 1).
``resolve_deadletter_level`` (ADR-021, seguimiento no-bloqueante Fase 5) determina el nivel del
                              objeto referenciado por un dead-letter, sin lanzar — single source
                              of truth compartida por el gate de detalle y el filtro del listado.
``resolve_deadletter_levels`` (FIX 2, mismo seguimiento) resuelve el nivel de VARIAS filas con
                              una sola consulta batch — misma semántica fail-closed, solo para
                              el listado (el gate de detalle sigue usando la versión per-row).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

from app.core.database import get_tenant_conn

try:
    from orpycamcp_common import audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "notification-service"
logger = logging.getLogger(__name__)

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

        @router.post("/...", dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])

    Or at router level to protect all routes::

        router = APIRouter(dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])
    """

    async def _dep(
        actor: uuid.UUID = Depends(get_actor),
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        if not await check_permission(conn, actor, perm_name, min_crud):
            # Traza best-effort del acceso denegado (RF-SEG / MoReq). ``actor``
            # aquí SIEMPRE está identificado — ``get_actor`` ya devolvió 401 si
            # faltaba/era inválido X-User-Id — así que no hay caso anónimo que
            # excluir (a diferencia de document/archive/workflow).
            if _AUDIT_AVAILABLE:
                try:
                    await audit.append_denial(
                        conn,
                        tenant_slug=x_tenant_slug,
                        service=_SERVICE,
                        actor=str(actor),
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
                "detail": "Falta la cabecera X-User-Id",
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
                "detail": "La cabecera X-User-Id debe ser un UUID válido (auth_users.id interno)",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_ref(row: asyncpg.Record) -> tuple[list[str], int | None]:
    """Parses ``row["payload"]`` into ``(identifiers, nivel_embebido)``.

    Shared by :func:`resolve_deadletter_level` (per-row) and
    :func:`resolve_deadletter_levels` (batch) so both stay byte-identical by
    construction instead of by discipline — a future edit to the extraction
    logic only has one place to happen. ``identifiers`` is ordered
    ``[tracking_number, id]`` (only the non-``None`` ones), matching the
    lookup order used by the live ``SELECT ... WHERE tracking_number = $1 OR
    id::text = $1`` query below. May raise (invalid JSON, non-dict payload);
    callers are responsible for the fail-closed wrapping.
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
    """Resuelve el nivel de seguridad (1..3) del objeto referenciado por un dead-letter.

    **Single source of truth** de la determinación de nivel, reutilizada tanto por
    el gate de detalle (:func:`assert_can_read_deadletter`) como por el filtrado
    por-fila del listado (ADR-021, seguimiento no-bloqueante Fase 5): ambos deben
    coincidir exactamente para que un admin nunca vea en el listado una fila que
    el detalle le negaría (o viceversa).

    Nunca lanza: cualquier duda se resuelve **fail-closed** a :data:`CLASIFICADA`
    (nivel máximo) devolviéndolo como valor de retorno, no como excepción — los
    llamantes (gate de detalle, filtro de listado) deciden qué hacer con el nivel
    resuelto.

    Reglas de determinación de nivel (fail-closed a CLASIFICADA=3 ante cualquier duda):
      - Sin identificadores de radicado en el sobre → CLASIFICADA.
      - Radicado no encontrado (o tabla ``radicados`` ausente) → CLASIFICADA.
      - En otro caso → ``max(nivel_vivo, nivel_embebido)`` (o ``nivel_vivo`` si no
        hay nivel embebido).

    ``row["payload"]`` es el sobre completo tal como lo persiste
    ``stream_worker._persist_dead_letter`` (``event_id``, ``event_type``,
    ``tenant_slug``, ``actor``, ``payload`` de negocio anidado). El nivel embebido
    se busca bajo llaves compatibles con los distintos eventos que notification
    consume (``nivel_nuevo`` para reclasificación, ``nivel_seguridad`` para
    radicación) — ninguno de los eventos actuales de notification embebe un nivel,
    por lo que en la práctica esto casi siempre recurre al nivel vivo del radicado.
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
        # FIX 3: nivel_embebido viene del sobre JSON (no tipado) y puede ser
        # un valor no interpretable como entero (texto libre, envelope
        # corrupto). int() lanzaría ValueError/TypeError → 500. Fail-closed:
        # un nivel embebido no interpretable se trata como CLASIFICADA
        # (máximo), coherente con el resto de este gate.
        try:
            return max(int(nivel_vivo), int(nivel_embebido))
        except (ValueError, TypeError):
            return CLASIFICADA
    return int(nivel_vivo)


async def resolve_deadletter_levels(
    conn: asyncpg.Connection,
    rows: Sequence[asyncpg.Record],
) -> dict[str, int]:
    """Resuelve el nivel de seguridad de VARIAS filas dead-letter con UNA sola query.

    FIX 2 (ADR-021, seguimiento no-bloqueante Fase 5): :func:`resolve_deadletter_level`
    hace un ``SELECT`` a ``radicados`` por fila — O(N) round-trips en el listado. Esta
    función resuelve el mismo resultado para un lote de filas con un único ``SELECT
    ... WHERE tracking_number = ANY($1) OR id::text = ANY($1)``.

    **Contrato de equivalencia** (byte-idéntico, fila a fila): para toda fila ``r``,
    ``resolve_deadletter_levels(conn, [r])[r["event_id"]]`` es exactamente el valor
    que devolvería (o CLASIFICADA si lanzara) ``resolve_deadletter_level(conn, r)``.
    :func:`resolve_deadletter_level` sigue siendo la fuente de verdad del gate de
    DETALLE (:func:`assert_can_read_deadletter`); esta función es su contraparte de
    SOLO LISTADO — nunca lanza, cualquier fila cuyo nivel no pueda determinarse con
    certeza cae a CLASIFICADA (fail-closed), igual que el 404 neutro del detalle.

    Devuelve ``{event_id: nivel}`` para TODAS las filas de entrada (una entrada por
    fila, incluidas las fail-closed).
    """
    # Fase 1: parseo por fila. Cualquier fila cuyo payload no se pueda parsear, o
    # que no traiga identificador alguno, se resuelve aquí mismo a CLASIFICADA y no
    # participa en la consulta batch (no aporta identificadores que buscar).
    per_row_refs: dict[str, tuple[list[str], int | None]] = {}
    levels: dict[str, int] = {}
    all_identifiers: set[str] = set()
    for row in rows:
        event_id = row["event_id"]
        try:
            identifiers, nivel_embebido = _extract_ref(row)
        except Exception:  # noqa: BLE001 — fail-closed: unparseable envelope.
            levels[event_id] = CLASIFICADA
            continue
        if not identifiers:
            # Sin identificador de radicado en el sobre → fail-closed.
            levels[event_id] = CLASIFICADA
            continue
        per_row_refs[event_id] = (identifiers, nivel_embebido)
        all_identifiers.update(identifiers)

    # Fase 2: UNA consulta para todos los identificadores acumulados. Tabla ausente
    # (notification-service no tiene `radicados`) se trata como "nada hallado" —
    # exactamente lo mismo que el UndefinedTableError capturado en el bucle per-row.
    level_by_ident: dict[str, int] = {}
    if all_identifiers:
        try:
            recs = await conn.fetch(
                "SELECT nivel_seguridad, tracking_number, id FROM radicados "
                "WHERE tracking_number = ANY($1::text[]) OR id::text = ANY($1::text[])",
                list(all_identifiers),
            )
        except asyncpg.UndefinedTableError:
            recs = []
        for rec in recs:
            level_by_ident[rec["tracking_number"]] = rec["nivel_seguridad"]
            level_by_ident[str(rec["id"])] = rec["nivel_seguridad"]

    # Fase 3: resolver cada fila parseable contra el mapa batch, preservando
    # exactamente la misma semántica fail-closed que resolve_deadletter_level.
    for event_id, (identifiers, nivel_embebido) in per_row_refs.items():
        try:
            nivel_vivo: int | None = None
            for ident in identifiers:
                if ident in level_by_ident:
                    nivel_vivo = level_by_ident[ident]
                    break
            if nivel_vivo is None:
                # Radicado no hallado → nunca degradar a público.
                levels[event_id] = CLASIFICADA
                continue
            if nivel_embebido is not None:
                # nivel_embebido no tipado puede no ser interpretable como entero
                # (envelope corrupto) → fail-closed, coherente con el per-row.
                levels[event_id] = max(int(nivel_vivo), int(nivel_embebido))
            else:
                levels[event_id] = int(nivel_vivo)
        except Exception:  # noqa: BLE001 — fail-closed: any per-row resolution error.
            levels[event_id] = CLASIFICADA

    return levels


async def assert_can_read_deadletter(
    conn: asyncpg.Connection,
    caller_clearance: int,
    row: asyncpg.Record,
) -> None:
    """No-read-up gate sobre un dead-letter (ADR-021 Fase 5, RF-SEG-08).

    Delega la determinación de nivel en :func:`resolve_deadletter_level` (single
    source of truth compartida con el filtrado del listado). Si el llamante no
    alcanza ese nivel, responde **404 neutro** (no 403): no se revela ni la
    existencia ni el nivel del objeto.
    """
    nivel_objeto = await resolve_deadletter_level(conn, row)
    if caller_clearance < nivel_objeto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "El evento en cola de fallos no existe o no es accesible",
                "status": 404,
            },
        )
