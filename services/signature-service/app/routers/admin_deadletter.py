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

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.clearance import (
    get_actor,
    require_permission,
    resolve_caller_clearance,
)
from app.core.database import get_tenant_conn
from app.repositories.dead_letter_repository import DeadLetterRepository
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

logger = logging.getLogger(__name__)

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
    if not isinstance(value, (str, bytes, bytearray)):
        # Ni dict, ni None, ni texto: `json.loads` lanzaría un TypeError opaco
        # a mitad de una respuesta de la DLQ. Se devuelve vacío, que es lo que
        # esta función ya promete como "defensa robusta".
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
    """Lista resúmenes de dead-letters del tenant (sin payload ni last_error).

    No-read-up (RF-SEG-08, AREA-10-08): el clearance del llamante se evalúa contra el
    radicado VIVO en el ``WHERE`` de la consulta (``orpycamcp_common.deadletter``),
    antes de paginar y de contar. Una fila por encima de su nivel no aparece ni suma al
    ``X-Total-Count``, y no hay tope de candidatas: la página y el total son exactos.
    Ante cualquier duda sobre el nivel de una fila se trata como CLASIFICADA (3).
    Si ``radicados`` no existe en el schema responde 503 (no una lista vacía).
    """
    caller_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DeadLetterRepository(conn)
    caller_clearance = await resolve_caller_clearance(conn, actor)

    page_rows, total = await repo.list_visible(
        caller_slug,
        GROUP,
        caller_clearance,
        status=status_filter,
        origin_stream=origin_stream,
        offset=(page - 1) * size,
        limit=size,
    )
    response.headers["X-Total-Count"] = str(total)
    return [DeadLetterSummary.model_validate(dict(row)) for row in page_rows]


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
    caller_clearance = await resolve_caller_clearance(conn, actor)
    row = await repo.get_by_event_id(event_id, caller_slug, GROUP, caller_clearance)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "status": 404},
        )


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
                payload={
                    "previous_status": row["status"],
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                },
                canal=_audit.CANAL_PETICION,
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
        caller_clearance = await resolve_caller_clearance(conn, actor)
        row = await repo.get_for_update(event_id, caller_slug, GROUP, caller_clearance)
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
                    "detail": "El evento pertenece a otra entidad",
                    "status": 422,
                },
            )


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
                    "detail": "Otro administrador ya resolvió el evento",
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
                payload={
                    "previous_status": row["status"],
                    "new_status": "resolved",
                    "outcome": {"rows_affected": rows_affected},
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                    "original_failure": failure_dict,
                },
                canal=_audit.CANAL_PETICION,
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
        caller_clearance = await resolve_caller_clearance(conn, actor)
        row = await repo.get_for_update(event_id, caller_slug, GROUP, caller_clearance)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "status": 404},
            )


        previous_status = row["status"]
        rows_affected = await repo.transition_status(event_id, caller_slug, "discarded", GROUP)
        if rows_affected == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "already_resolved",
                    "detail": "El evento ya no está pendiente",
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
                payload={
                    "previous_status": previous_status,
                    "new_status": "discarded",
                    "reason": body.reason,
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                    "original_failure": failure_dict,
                },
                canal=_audit.CANAL_PETICION,
            )

    return DeadLetterActionResult(
        event_id=event_id,
        previous_status=previous_status,
        new_status="discarded",
        outcome={"rows_affected": rows_affected},
    )
