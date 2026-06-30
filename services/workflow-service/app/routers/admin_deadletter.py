# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Admin DLQ inspection and replay router (ADR-021 Fase 5, PERM_DLQ_ADMIN).

Espejo del router de signature-service adaptado al patrón de workflow-service:
``get_db_conn`` (sin search_path automático) + ``require_permission`` fija el
search_path al tenant sobre la misma conexión cacheada por request, de modo que
el endpoint ve el schema correcto. El replay re-invoca ``apply_distribution`` en
la MISMA transacción que la transición de estado y la auditoría.
"""

from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger(__name__)

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from app.core.clearance import (
    assert_can_read_deadletter,
    require_permission,
    resolve_caller_clearance,
)
from app.core.database import get_db_conn
from app.repositories.dead_letter_repository import DeadLetterRepository
from app.schemas.dead_letter import (
    DeadLetterActionResult,
    DeadLetterDetail,
    DeadLetterSummary,
    DiscardRequest,
)
from app.workers.document_consumer import apply_distribution

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "workflow-service"

router = APIRouter(
    prefix="/api/v1/workflow/admin/deadletter",
    tags=["admin-dlq"],
    dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))],
)


def _actor(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    """Extrae el UUID interno del llamante desde X-User-Id (None si ausente/inválido)."""
    if not x_user_id:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        return None


def _as_dict(value: object) -> dict:
    """asyncpg JSONB → dict (ya parseado), o str → json.loads. Defensa robusta."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return json.loads(value)


@router.get("/", response_model=list[DeadLetterSummary])
async def list_dead_letters(
    request: Request,
    response: Response,
    status_filter: str = Query(default="pending", alias="status"),
    origin_stream: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[DeadLetterSummary]:
    """Lista resúmenes de dead-letters del tenant (sin payload ni last_error)."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DeadLetterRepository(conn)
    offset = (page - 1) * size
    rows, total = await repo.list_pending(
        caller_slug,
        status=status_filter,
        origin_stream=origin_stream,
        offset=offset,
        limit=size,
    )
    response.headers["X-Total-Count"] = str(total)
    return [DeadLetterSummary.model_validate(dict(r)) for r in rows]


@router.get("/{event_id}", response_model=DeadLetterDetail)
async def get_dead_letter(
    event_id: str,
    request: Request,
    actor: uuid.UUID | None = Depends(_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> DeadLetterDetail:
    """Detalle completo de un dead-letter (payload + failure), tras gate de clearance."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DeadLetterRepository(conn)
    row = await repo.get_by_event_id(event_id, caller_slug)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "status": 404},
        )

    caller_clearance = await resolve_caller_clearance(conn, actor)
    await assert_can_read_deadletter(conn, caller_clearance, row)

    return DeadLetterDetail(
        event_id=row["event_id"],
        event_type=row["event_type"],
        correlation_id=row["correlation_id"],
        tenant_slug=row["tenant_slug"],
        origin_stream=row["origin_stream"],
        origin_group=row["origin_group"],
        status=row["status"],
        dead_lettered_at=row["dead_lettered_at"],
        resolved_at=row["resolved_at"],
        payload=_as_dict(row["payload"]),
        failure=_as_dict(row["failure"]),
    )


@router.post("/{event_id}/replay", response_model=DeadLetterActionResult)
async def replay_dead_letter(
    event_id: str,
    request: Request,
    actor: uuid.UUID | None = Depends(_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> DeadLetterActionResult:
    """Reprocesa un dead-letter pendiente de forma atómica (handler + estado + audit)."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")

    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        # H7: SELECT FOR UPDATE con predicado de tenant.
        row = await repo.get_for_update(event_id, caller_slug)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "status": 404},
            )

        # H3: el sobre debe pertenecer al tenant llamante (defensa adicional).
        envelope_payload = _as_dict(row["payload"])
        envelope_tenant = envelope_payload.get("tenant_slug", "")
        if envelope_tenant != caller_slug or row["tenant_slug"] != caller_slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "tenant_mismatch",
                    "detail": "Event tenant does not match caller tenant",
                    "status": 422,
                },
            )

        # H1: no-read-up por clearance.
        caller_clearance = await resolve_caller_clearance(conn, actor)
        await assert_can_read_deadletter(conn, caller_clearance, row)

        inner_payload = envelope_payload.get("payload", {}) or {}

        # H3: re-invoca el handler-core en la MISMA conexión/transacción.
        try:
            assigned_to = await apply_distribution(conn, caller_slug, inner_payload)
        except Exception as exc:  # noqa: BLE001
            # El fallo del handler revierte la transacción (rollback) → queda pending.
            logger.exception(
                "DLQ replay failed for event_id=%s", event_id, exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "replay_failed",
                    "detail": (
                        "El reprocesamiento del evento falló;"
                        " el dead-letter permanece pendiente."
                    ),
                    "status": 502,
                },
            ) from exc

        # H5: transición pending → resolved (0 filas = ya cerrado por otro admin).
        rows_affected = await repo.transition_status(event_id, caller_slug, "resolved")
        if rows_affected == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "already_resolved",
                    "detail": "Event was already resolved by another admin",
                    "status": 409,
                },
            )

        if _AUDIT_AVAILABLE:
            failure_dict = _as_dict(row["failure"])
            await _audit.append(
                conn,
                tenant_slug=caller_slug,
                service=_SERVICE,
                action="evento.dead_letter.replayed",
                object_type="evento",
                object_ref=event_id,
                actor=str(actor) if actor else None,
                canal="api",
                payload={
                    "previous_status": row["status"],
                    "new_status": "resolved",
                    "outcome": {"rows_affected": rows_affected, "assigned_to": assigned_to},
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                    "original_failure": failure_dict,
                },
            )

    return DeadLetterActionResult(
        event_id=event_id,
        previous_status=row["status"],
        new_status="resolved",
        outcome={"rows_affected": rows_affected, "assigned_to": assigned_to},
    )


@router.post("/{event_id}/discard", response_model=DeadLetterActionResult)
async def discard_dead_letter(
    event_id: str,
    body: DiscardRequest,
    request: Request,
    actor: uuid.UUID | None = Depends(_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> DeadLetterActionResult:
    """Descarta deliberadamente un dead-letter pendiente (con motivo, auditado)."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")

    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        # SELECT FOR UPDATE: symmetric with replay; prevents concurrent discard/replay race.
        row = await repo.get_for_update(event_id, caller_slug)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "status": 404},
            )

        # INC1-1: no-write-up clearance gate (404 neutro, symmetric with replay/detail).
        caller_clearance = await resolve_caller_clearance(conn, actor)
        await assert_can_read_deadletter(conn, caller_clearance, row)

        previous_status = row["status"]
        rows_affected = await repo.transition_status(event_id, caller_slug, "discarded")
        if rows_affected == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "already_resolved",
                    "detail": "Event is not pending",
                    "status": 409,
                },
            )

        if _AUDIT_AVAILABLE:
            failure_dict = _as_dict(row["failure"])
            await _audit.append(
                conn,
                tenant_slug=caller_slug,
                service=_SERVICE,
                action="evento.dead_letter.discarded",
                object_type="evento",
                object_ref=event_id,
                actor=str(actor) if actor else None,
                canal="api",
                payload={
                    "previous_status": previous_status,
                    "new_status": "discarded",
                    "reason": body.reason,
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                    "original_failure": failure_dict,
                },
            )

    return DeadLetterActionResult(
        event_id=event_id,
        previous_status=previous_status,
        new_status="discarded",
        outcome={"rows_affected": rows_affected},
    )
