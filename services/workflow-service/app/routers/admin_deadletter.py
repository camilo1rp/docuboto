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

``evento_dead_letter`` es una tabla compartida con signature-service (ver nota de
módulo en ``app/repositories/dead_letter_repository.py``); todas las consultas de
este router pasan ``GROUP`` (``app.core.constants.DLQ_GROUP``) para acotarse a los
dead-letters generados por ESTE servicio.
"""

from __future__ import annotations

import json
import logging
import uuid

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
    get_actor,
    require_permission,
    resolve_caller_clearance,
    resolve_deadletter_levels,
)
from app.core.constants import DLQ_GROUP as GROUP
from app.core.database import get_db_conn
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
from app.workers.document_consumer import apply_distribution

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

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
    if not isinstance(value, (str, bytes, bytearray)):
        # Ni dict, ni None, ni texto: `json.loads` lanzaría un TypeError opaco.
        # Se devuelve vacío, que es lo que esta función ya promete tolerar.
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
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[DeadLetterSummary]:
    """Lista resúmenes de dead-letters del tenant (sin payload ni last_error).

    No-read-up (ADR-021, seguimiento no-bloqueante Fase 5): un admin PERM_DLQ_ADMIN
    con clearance insuficiente para el objeto referenciado por una fila no debe ver
    ni siquiera su EXISTENCIA/conteo aquí — de lo contrario el listado filtraría
    información que el detalle (``GET /{event_id}``) ya oculta tras el gate de
    clearance (404 neutro). El nivel de TODAS las candidatas se resuelve EN LOTE
    con ``resolve_deadletter_levels`` (ADR-021, pulido Fase 5 "Baja" [2]: una sola
    consulta a ``radicados`` en vez de N; equivalente fila a fila a llamar
    ``resolve_deadletter_level`` por cada una — single source of truth compartida
    con el gate del detalle, ver ``app/core/clearance.py``) y se descartan las
    filas que superan el clearance del llamante ANTES de paginar, de modo que
    ``X-Total-Count`` y la página devuelta ya reflejan el conjunto visible.
    Fail-closed: cualquier duda al resolver el nivel de una fila la excluye (se
    trata como CLASIFICADA).

    Identidad (ADR-021, pulido Fase 5 "Baja" [3]): a diferencia de detail/replay/
    discard (que siguen usando el ``_actor`` local, opcional), el listado exige
    ``X-User-Id`` vía ``get_actor`` (401 si falta/ inválido) — unifica la fuente
    de identidad con signature-service/notification-service. En la práctica el
    gate ``require_permission`` (dependencia de router, se resuelve antes) ya
    garantiza un ``X-User-Id`` válido con permiso, así que esta rama 401 no es
    observable hoy en las rutas ya protegidas — ver docstring de ``get_actor``.

    Cap de candidatas (ADR-021, pulido Fase 5 "Baja" [1]): ``list_scoped_for_clearance``
    acota el conjunto candidato a ``DEADLETTER_CANDIDATE_CAP`` filas (LIMIT en SQL,
    antes del filtro de clearance). Si se alcanza el cap, las filas pendientes más
    antiguas por encima de ese tope quedan invisibles y ``X-Total-Count`` puede
    sub-reportar — se señala con un ``log.warning`` y el header de respuesta
    ``X-Truncated: true`` (ausente cuando no se trunca).
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
    visible = [row for row in candidates if caller_clearance >= levels[row["event_id"]]]

    total = len(visible)
    offset = (page - 1) * size
    page_rows = visible[offset : offset + size]

    response.headers["X-Total-Count"] = str(total)
    summaries = []
    for row in page_rows:
        summary_data = dict(row)
        summary_data.pop("payload", None)  # nunca serializar el sobre en el resumen
        summaries.append(DeadLetterSummary.model_validate(summary_data))
    return summaries


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
    row = await repo.get_by_event_id(event_id, caller_slug, GROUP)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "status": 404},
        )

    caller_clearance = await resolve_caller_clearance(conn, actor)
    await assert_can_read_deadletter(conn, caller_clearance, row)

    # Auditoría de vista (ADR-021, seguimiento no-bloqueante Fase 5): solo se
    # audita DESPUÉS del gate, es decir, solo vistas AUTORIZADAS — una vista
    # bloqueada devuelve 404 y no debe dejar rastro que confirme la existencia
    # del objeto clasificado. Transacción propia porque el GET no abre una (a
    # diferencia de replay/discard, que sí envuelven su INSERT de auditoría).
    if _AUDIT_AVAILABLE:
        async with conn.transaction():
            await _audit.append(
                conn,
                tenant_slug=caller_slug,
                service=_SERVICE,
                action="evento.dead_letter.viewed",
                object_type="evento",
                object_ref=event_id,
                actor=str(actor) if actor else None,
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
    actor: uuid.UUID | None = Depends(_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> DeadLetterActionResult:
    """Reprocesa un dead-letter pendiente de forma atómica (handler + estado + audit)."""
    caller_slug = request.headers.get("X-Tenant-Slug", "")

    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        # H7: SELECT FOR UPDATE con predicado de tenant + origin_group.
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
                    "detail": "El evento pertenece a otra entidad",
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
        rows_affected = await repo.transition_status(
            event_id, caller_slug, "resolved", GROUP
        )
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
        rows_affected = await repo.transition_status(
            event_id, caller_slug, "discarded", GROUP
        )
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
