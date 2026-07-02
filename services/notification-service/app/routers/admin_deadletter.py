# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Admin DLQ inspection and replay router (ADR-021 Fase 5 Inc.2, PERM_DLQ_ADMIN).

Inspección y reproceso del dead-letter durable (``evento_dead_letter``) para
notification-service. Todas las rutas exigen ``PERM_DLQ_ADMIN`` (gate a nivel de
router) y aplican el no-read-up por clearance sobre el radicado referenciado
(404 neutro).

ADR-021 (seguimiento no-bloqueante Fase 5): el LISTADO también filtra por
clearance, fila a fila, reutilizando ``resolve_deadletter_level`` (misma
resolución que el gate de detalle) — un admin sub-clearance no ve ni la
existencia ni el conteo de dead-letters sobre objetos clasificados. El DETALLE
(GET /{event_id}), tras superar el gate, deja un asiento de auditoría
``evento.dead_letter.viewed`` (una vista bloqueada devuelve 404 y NO audita).

Diferencia clave frente a signature/workflow: el efecto de notification es enviar
un EMAIL (SMTP, externo, NO transaccional), por lo que el replay no puede ser
atómico como ``apply_reclassification`` (handler + estado + audit en una sola tx
sin efectos externos). El diseño elegido es **claim-then-send**: el envío ocurre
DENTRO de la transacción (con la fila bloqueada por ``FOR UPDATE``), pero la
transición a ``resolved`` sólo se aplica si TODOS los envíos de esta fila
resultaron en ``status == "sent"``. Si cualquiera falla, se lanza 502 y la
transacción se revierte — el dead-letter permanece ``pending`` (fail-safe: nunca
marcar resuelto un evento que no se envió; el trade-off aceptado es un posible
DOBLE ENVÍO si el commit posterior a un envío exitoso fallara, preferible a una
pérdida silenciosa — ver ADR-021 Fase 5 Inc.2, A-1).
"""

from __future__ import annotations

import json
import logging
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.clearance import (
    CLASIFICADA,
    assert_can_read_deadletter,
    get_actor,
    require_permission,
    resolve_caller_clearance,
    resolve_deadletter_level,
)
from app.core.constants import DLQ_GROUP
from app.core.database import get_tenant_conn
from app.repositories.dead_letter_repository import DeadLetterRepository
from app.repositories.processed_events_repository import ProcessedEventsRepository
from app.schemas.dead_letter import (
    DeadLetterActionResult,
    DeadLetterDetail,
    DeadLetterSummary,
    DiscardRequest,
)
from app.services.event_consumer import (
    build_document_notifications,
    build_workflow_notifications,
)
from app.services.notification_service import send_notification
from app.workers.stream_worker import DOCUMENT_STREAM, WORKFLOW_STREAM

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "notification-service"

# Maps the stream a dead-lettered event originated from to the pure builder that
# reconstructs the NotificationSend(s) for it (same builders the live consumer
# uses — see app/services/event_consumer.py). A row whose origin_stream is
# missing or does not match either known stream cannot be routed and fails
# closed (422) rather than guessing which handler applies.
_BUILDERS = {
    WORKFLOW_STREAM: build_workflow_notifications,
    DOCUMENT_STREAM: build_document_notifications,
}

router = APIRouter(
    prefix="/api/v1/notification/admin/deadletter",
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
    """Lista resúmenes de dead-letters del tenant, filtrados por clearance del llamante.

    ADR-021 (seguimiento no-bloqueante Fase 5): resolución de nivel POR-FILA sobre
    el nivel VIVO del radicado referenciado, reutilizando exactamente la misma
    función que el gate de detalle (:func:`resolve_deadletter_level`) — así el
    listado nunca revela la existencia (ni el conteo) de un dead-letter que el
    detalle negaría con 404. Fail-closed: cualquier error al resolver el nivel de
    una fila la excluye (se trata como CLASIFICADA).

    La paginación se aplica EN APP sobre el conjunto ya filtrado: no se puede
    paginar en SQL antes de saber cuántas filas son visibles para este llamante.
    ``X-Total-Count`` refleja el total POST-filtro, no el total crudo de la tabla.
    """
    caller_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DeadLetterRepository(conn)
    caller_clearance = await resolve_caller_clearance(conn, actor)

    candidates = await repo.list_scoped_for_clearance(
        caller_slug,
        DLQ_GROUP,
        status=status_filter,
        origin_stream=origin_stream,
    )

    visible: list[asyncpg.Record] = []
    for row in candidates:
        try:
            nivel = await resolve_deadletter_level(conn, row)
        except Exception:  # noqa: BLE001 — fail-closed: any resolution error hides the row.
            logger.warning(
                "resolve_deadletter_level failed for event_id=%s; excluding from listing "
                "(fail-closed)",
                row["event_id"],
                exc_info=True,
            )
            nivel = CLASIFICADA
        if caller_clearance >= nivel:
            visible.append(row)

    total = len(visible)
    offset = (page - 1) * size
    page_rows = visible[offset : offset + size]
    response.headers["X-Total-Count"] = str(total)
    return [
        DeadLetterSummary.model_validate(
            {k: v for k, v in dict(r).items() if k != "payload"}
        )
        for r in page_rows
    ]


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
    row = await repo.get_by_event_id(event_id, caller_slug, DLQ_GROUP)
    if row is None:
        # FIX 1: byte-identical to the clearance-denied 404 raised by
        # assert_can_read_deadletter below — "does not exist" and "exists but
        # caller lacks clearance" must be indistinguishable (no-read-up, RF-SEG-08).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "Dead-letter event not found or not accessible",
                "status": 404,
            },
        )

    caller_clearance = await resolve_caller_clearance(conn, actor)
    await assert_can_read_deadletter(conn, caller_clearance, row)

    # ADR-021 (seguimiento no-bloqueante Fase 5): traza de acceso a detalle.
    # Solo se audita DESPUÉS del gate — una vista bloqueada devuelve 404 y NO
    # audita, para no confirmar la existencia del evento a quien no tiene
    # clearance. Payload MÍNIMO (sin payload/failure de negocio) para no filtrar
    # contenido clasificado a través del propio audit_log.
    if _AUDIT_AVAILABLE:
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
    """Reprocesa (reenvía) un dead-letter pendiente — best-effort claim-then-send.

    A diferencia de signature/workflow, el "handler" reenviado tiene un efecto
    externo NO transaccional (SMTP): la transición a ``resolved`` sólo ocurre si
    TODOS los envíos derivados de este evento tuvieron éxito. Ver el docstring
    de módulo para el trade-off aceptado (posible doble-envío vs. pérdida
    silenciosa).
    """
    caller_slug = request.headers.get("X-Tenant-Slug", "")

    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        # H7: SELECT FOR UPDATE con predicado de tenant + origin_group.
        row = await repo.get_for_update(event_id, caller_slug, DLQ_GROUP)
        if row is None:
            # FIX 1: same 404 body as the clearance-denied branch below (no-read-up).
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "not_found",
                    "detail": "Dead-letter event not found or not accessible",
                    "status": 404,
                },
            )

        envelope = _as_dict(row["payload"])
        envelope_tenant = envelope.get("tenant_slug", "")
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

        # FIX 2(a): fail-closed audit dependency. Without orpycamcp_common.audit
        # a state transition (and, on this path, an outbound email) would commit
        # with NO legal trace — and purge_terminal would later delete the sole
        # evento_dead_letter row, erasing all evidence. Refuse before doing
        # anything externally-visible (SMTP) or mutating state.
        if not _AUDIT_AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "audit_unavailable",
                    "detail": (
                        "El módulo de auditoría no está disponible; se rechaza "
                        "el reprocesamiento para no transicionar sin traza legal."
                    ),
                    "status": 503,
                },
            )

        # Route to the same pure builder the live consumer uses for this
        # origin_stream. An unknown/missing origin_stream cannot be routed
        # safely — fail closed rather than guess (open_risk in the contract).
        builder = _BUILDERS.get(row["origin_stream"])
        if builder is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "unknown_origin_stream",
                    "detail": (
                        f"No se puede reprocesar: origin_stream={row['origin_stream']!r} "
                        "no corresponde a un stream conocido"
                    ),
                    "status": 422,
                },
            )
        sends = builder(envelope)

        # Clear any stale dedup claim before resending (defensive: see the module
        # note in app/services/event_consumer.py on why most notification
        # dead-letters never reach the claim in the first place). Runs inside the
        # same transaction: a rollback below (failed send) restores this row too,
        # keeping dedup state and dead-letter status consistent.
        await ProcessedEventsRepository(conn).delete_one(event_id)

        # Claim-then-send: SMTP happens while the row is locked (FOR UPDATE) and
        # the transaction is open — accepted for a low-frequency admin action
        # (see module docstring). send_notification does NOT raise on SMTP
        # failure; it returns a Notification whose .status is 'sent' or 'failed'
        # — the gate below MUST inspect .status, not rely on an exception.
        try:
            sent = [
                await send_notification(send, event_type=envelope.get("event_type"))
                for send in sends
            ]
        except Exception as exc:  # noqa: BLE001 — defensive; send_notification should not raise
            logger.exception("DLQ replay failed for event_id=%s", event_id, exc_info=True)
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

        failed = [n for n in sent if n.status != "sent"]
        if failed:
            # At least one email failed to send after retries — do NOT mark
            # resolved (would silently lose the failure). Rollback restores the
            # dedup claim removed above and leaves the row 'pending'.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "replay_failed",
                    "detail": (
                        f"{len(failed)} de {len(sent)} envíos fallaron;"
                        " el dead-letter permanece pendiente."
                    ),
                    "status": 502,
                },
            )

        # H5: transición pending → resolved (0 filas = ya cerrado por otro admin).
        rows_affected = await repo.transition_status(event_id, caller_slug, "resolved", DLQ_GROUP)
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
                    "outcome": {"rows_affected": rows_affected, "notifications_sent": len(sent)},
                    "event_type": row["event_type"],
                    "origin_stream": row["origin_stream"],
                    "original_failure": failure_dict,
                },
            )

    return DeadLetterActionResult(
        event_id=event_id,
        previous_status=row["status"],
        new_status="resolved",
        outcome={"rows_affected": rows_affected, "notifications_sent": len(sent)},
    )


@router.post("/{event_id}/discard", response_model=DeadLetterActionResult)
async def discard_dead_letter(
    event_id: str,
    body: DiscardRequest,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DeadLetterActionResult:
    """Descarta deliberadamente un dead-letter pendiente (con motivo, auditado).

    Simétrico al replay pero puramente transaccional (sin SMTP): FOR UPDATE →
    clearance → transición pending → discarded + audit con motivo.
    """
    caller_slug = request.headers.get("X-Tenant-Slug", "")

    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        # SELECT FOR UPDATE: symmetric with replay; prevents concurrent discard/replay race.
        row = await repo.get_for_update(event_id, caller_slug, DLQ_GROUP)
        if row is None:
            # FIX 1: same 404 body as the clearance-denied branch below (no-read-up).
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "not_found",
                    "detail": "Dead-letter event not found or not accessible",
                    "status": 404,
                },
            )

        # INC1-1: no-write-up clearance gate (404 neutro, symmetric with replay/detail).
        caller_clearance = await resolve_caller_clearance(conn, actor)
        await assert_can_read_deadletter(conn, caller_clearance, row)

        # FIX 2(a): fail-closed audit dependency — refuse to transition
        # pending → discarded without a durable audit trace (see replay for
        # the full rationale: purge_terminal later deletes the sole copy).
        if not _AUDIT_AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "audit_unavailable",
                    "detail": (
                        "El módulo de auditoría no está disponible; se rechaza "
                        "el descarte para no transicionar sin traza legal."
                    ),
                    "status": 503,
                },
            )

        previous_status = row["status"]
        rows_affected = await repo.transition_status(event_id, caller_slug, "discarded", DLQ_GROUP)
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
