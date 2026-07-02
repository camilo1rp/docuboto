# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Admin DLQ inspection and replay router (ADR-021 Fase 5, PERM_DLQ_ADMIN).

Inspección y reproceso del dead-letter durable (``evento_dead_letter``).
Todas las rutas exigen ``PERM_DLQ_ADMIN`` (gate a nivel de router) y aplican el
no-read-up por clearance sobre el radicado referenciado (404 neutro). El replay
re-invoca el handler-core (``apply_reclassification``) en la MISMA transacción que
la transición de estado y la auditoría: o commitean los tres, o no commitea nada.
"""

from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger(__name__)

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.clearance import (
    assert_can_read_deadletter,
    get_actor,
    require_permission,
    resolve_caller_clearance,
    resolve_deadletter_levels,
)
from app.core.database import get_tenant_conn
from app.repositories.dead_letter_repository import (
    DEADLETTER_CANDIDATE_CAP,
    DeadLetterRepository,
)
from app.schemas.dead_letter import (
    DeadLetterActionResult,
    DeadLetterDetail,
    DeadLetterSummary,
    DiscardRequest,
)
from app.workers.document_consumer import GROUP, apply_reclassification

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"

router = APIRouter(
    prefix="/api/v1/signature/admin/deadletter",
    tags=["admin-dlq"],
    dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))],
)


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
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DeadLetterSummary]:
    """Lista resúmenes de dead-letters del tenant, filtrados por clearance (no-read-up).

    ADR-021 Fase 5 (seguimiento): el listado aplica la MISMA semántica fail-closed
    que el gate del detalle (:func:`resolve_deadletter_level`) sobre el nivel VIVO
    del radicado referenciado, pero resuelta EN LOTE — ver más abajo. Una fila
    cuyo objeto excede el clearance del llamante se OMITE del todo (no aparece
    ni cuenta en ``X-Total-Count``) — consistente con el 404 neutro del
    detalle: nunca se revela existencia ni conteo de dead-letters sobre
    objetos clasificados a un admin sin el nivel suficiente.

    La paginación se aplica en la capa de aplicación (no en SQL) porque el
    tamaño del conjunto visible solo se conoce tras filtrar por clearance; ver
    :meth:`DeadLetterRepository.list_scoped_for_clearance`.

    ADR-021 seguimiento Baja [1]/[2]: la resolución de nivel se hace EN LOTE
    (:func:`resolve_deadletter_levels`, una sola consulta a ``radicados``) en
    vez de fila-a-fila (elimina el N+1); y si el número de candidatas alcanza
    el tope de seguridad del repositorio (``DEADLETTER_CANDIDATE_CAP``), la
    respuesta lo señala vía log y el header ``X-Truncated`` en vez de
    sub-reportar ``X-Total-Count`` en silencio.
    """
    caller_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DeadLetterRepository(conn)
    caller_clearance = await resolve_caller_clearance(conn, actor)

    candidates = await repo.list_scoped_for_clearance(
        caller_slug,
        GROUP,
        status=status_filter,
        origin_stream=origin_stream,
    )

    if len(candidates) >= DEADLETTER_CANDIDATE_CAP:
        # El tope se evalúa sobre el conjunto PRE-filtro de clearance (donde
        # aplica el LIMIT del SQL): las filas más antiguas más allá del tope
        # quedan invisibles y X-Total-Count (post-filtro) puede sub-reportar.
        logger.warning(
            "DLQ candidate cap reached: tenant=%s origin_group=%s status=%s "
            "cap=%d — oldest pending rows are invisible; consider keyset pagination",
            caller_slug,
            GROUP,
            status_filter,
            DEADLETTER_CANDIDATE_CAP,
        )
        response.headers["X-Truncated"] = "true"

    levels = await resolve_deadletter_levels(conn, candidates)
    visible: list[dict] = [
        dict(row) for row in candidates if caller_clearance >= levels[row["event_id"]]
    ]

    total = len(visible)
    offset = (page - 1) * size
    page_rows = visible[offset : offset + size]
    response.headers["X-Total-Count"] = str(total)

    summaries = []
    for row in page_rows:
        # Strip payload/failure explicitly before validating — do not rely on the
        # schema's extra="ignore" default to keep classified content out of the
        # response (defense in depth, ADR-021 Fase 5 note en DeadLetterSummary).
        row.pop("payload", None)
        row.pop("failure", None)
        summaries.append(DeadLetterSummary.model_validate(row))
    return summaries


@router.get("/{event_id}", response_model=DeadLetterDetail)
async def get_dead_letter(
    event_id: str,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DeadLetterDetail:
    """Detalle completo de un dead-letter (payload + failure), tras gate de clearance."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DeadLetterRepository(conn)
    row = await repo.get_by_event_id(event_id, caller_slug, GROUP)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "status": 404},
        )

    caller_clearance = await resolve_caller_clearance(conn, actor)
    await assert_can_read_deadletter(conn, caller_clearance, row)

    if _AUDIT_AVAILABLE:
        # Solo se audita una vista AUTORIZADA (tras el gate): un intento
        # bloqueado devuelve 404 y no debe dejar rastro que confirme la
        # existencia del evento (ADR-021 Fase 5, seguimiento de auditoría).
        async with conn.transaction():
            await _audit.append(
                conn,
                tenant_slug=caller_slug,
                service=_SERVICE,
                action="evento.dead_letter.viewed",
                object_type="evento",
                object_ref=event_id,
                actor=str(actor),
                canal="api",
                payload={
                    "previous_status": row["status"],
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                },
            )

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
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DeadLetterActionResult:
    """Reprocesa un dead-letter pendiente de forma atómica (handler + estado + audit)."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")

    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        # H7: SELECT FOR UPDATE con predicado de tenant.
        row = await repo.get_for_update(event_id, caller_slug, GROUP)
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
            await apply_reclassification(
                conn,
                caller_slug,
                inner_payload,
                actor=str(actor),
                replayed=True,
            )
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
        rows_affected = await repo.transition_status(event_id, caller_slug, "resolved", GROUP)
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
                actor=str(actor),
                canal="api",
                payload={
                    "previous_status": row["status"],
                    "new_status": "resolved",
                    "outcome": {"rows_affected": rows_affected},
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                    "original_failure": failure_dict,
                },
            )

    return DeadLetterActionResult(
        event_id=event_id,
        previous_status=row["status"],
        new_status="resolved",
        outcome={"rows_affected": rows_affected},
    )


@router.post("/{event_id}/discard", response_model=DeadLetterActionResult)
async def discard_dead_letter(
    event_id: str,
    body: DiscardRequest,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DeadLetterActionResult:
    """Descarta deliberadamente un dead-letter pendiente (con motivo, auditado)."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")

    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        # SELECT FOR UPDATE: symmetric with replay; prevents concurrent discard/replay race.
        row = await repo.get_for_update(event_id, caller_slug, GROUP)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "status": 404},
            )

        # INC1-1: no-write-up clearance gate (404 neutro, symmetric with replay/detail).
        caller_clearance = await resolve_caller_clearance(conn, actor)
        await assert_can_read_deadletter(conn, caller_clearance, row)

        previous_status = row["status"]
        rows_affected = await repo.transition_status(event_id, caller_slug, "discarded", GROUP)
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
                actor=str(actor),
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
