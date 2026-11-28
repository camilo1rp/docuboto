# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La consola del dead-letter: una para los cuatro grupos (ADR-021 Fase 5, ADR-049 §4).

``/api/v1/admin/deadletter/{grupo}``, con ``grupo`` en ``workflow``,
``notification``, ``signature`` o ``inteligencia``. Todas las rutas exigen
``PERM_DLQ_ADMIN`` y aplican el no-read-up por clearance en el ``WHERE``
(``orpycamcp_common.deadletter``): lo que no se ve no se lista, no cuenta en
``X-Total-Count`` y no se puede reprocesar ni descartar (404 neutro).

Listar, ver y descartar son iguales para todos los grupos. Reprocesar no: cada
grupo vuelve a ejecutar SU núcleo, en la transacción de la consola, a través de
:data:`REPLAYS`. La pasarela de inteligencia no tiene reproceso —su efecto es
una llamada al proveedor que el propio consumidor reconcilia—: sus dead-letters
se ven y se descartan, y el reproceso responde 409.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from orpycamcp_common import audit
from orpycamcp_common.clearance import get_actor
from orpycamcp_common.clearance import require_actor_permission as require_permission
from orpycamcp_common.database import get_tenant_conn
from orpycamcp_common.rbac import resolve_caller_clearance
from pydantic import BaseModel, ConfigDict, StringConstraints

from docuboto.core.dead_letter import DeadLetterRepository, ReplayFallido
from knowledge.workers.intelligence_consumer import GROUP as GRUPO_INTELIGENCIA
from notification.core.constants import DLQ_GROUP as GRUPO_NOTIFICATION
from notification.services import replay as notification_replay
from signature.core.constants import DLQ_GROUP as GRUPO_SIGNATURE
from signature.workers import document_consumer as signature_consumer
from workflow.core.constants import DLQ_GROUP as GRUPO_WORKFLOW
from workflow.workers import document_consumer as workflow_consumer

logger = logging.getLogger(__name__)

_SERVICE = "docuboto"

#: Nombre corto de la ruta → grupo consumidor (`origin_group` en la tabla).
GRUPOS: dict[str, str] = {
    "workflow": GRUPO_WORKFLOW,
    "notification": GRUPO_NOTIFICATION,
    "signature": GRUPO_SIGNATURE,
    "inteligencia": GRUPO_INTELIGENCIA,
}

Replay = Callable[..., Awaitable[dict[str, Any]]]

#: El núcleo que reprocesa cada grupo. Recibe la conexión de la consola (con la
#: transacción abierta) y devuelve el `outcome`; si lanza, todo se revierte.
REPLAYS: dict[str, Replay] = {
    "workflow": workflow_consumer.replay,
    "notification": notification_replay.replay,
    "signature": signature_consumer.replay,
}


# ── Esquemas ──────────────────────────────────────────────────────────────────


class DeadLetterSummary(BaseModel):
    """Sin payload ni último error: el resumen no revela objetos clasificados."""

    model_config = ConfigDict(from_attributes=True)
    event_id: str
    event_type: str | None = None
    origin_stream: str | None = None
    origin_group: str | None = None
    status: str
    dead_lettered_at: datetime
    resolved_at: datetime | None = None


class DeadLetterDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_id: str
    event_type: str | None = None
    correlation_id: str | None = None
    tenant_slug: str
    origin_stream: str | None = None
    origin_group: str | None = None
    status: str
    dead_lettered_at: datetime
    resolved_at: datetime | None = None
    payload: dict  # sobre íntegro (JSONB)
    failure: dict  # failure_meta (JSONB)


class DeadLetterActionResult(BaseModel):
    event_id: str
    previous_status: str
    new_status: str
    outcome: dict  # p. ej. {"rows_affected": 1, "notifications_sent": 1}


class DiscardRequest(BaseModel):
    # Un motivo sustantivo y trazable, no un carácter suelto: el recorte se aplica
    # ANTES de contar (MoReq / Ley 594 art. 23).
    reason: Annotated[str, StringConstraints(min_length=10, strip_whitespace=True)]


# ── Rutas ─────────────────────────────────────────────────────────────────────

router = APIRouter(
    prefix="/api/v1/admin/deadletter",
    tags=["admin-dlq"],
    dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))],
)


def _grupo(grupo: str = Path(...)) -> str:
    """El grupo de la ruta, o 404: una lista cerrada, nunca un valor libre."""
    if grupo not in GRUPOS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "grupo_desconocido", "status": 404},
        )
    return grupo


def _as_dict(value: object) -> dict:
    """JSONB de asyncpg → dict, ya decodificado o como texto. Lo demás, vacío."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str | bytes | bytearray):
        return {}
    return json.loads(value)


def _no_encontrado() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "not_found",
            "detail": "El evento en cola de fallos no existe o no es accesible",
            "status": 404,
        },
    )


@router.get("/{grupo}/", response_model=list[DeadLetterSummary])
async def list_dead_letters(
    request: Request,
    response: Response,
    grupo: str = Depends(_grupo),
    status_filter: str = Query(default="pending", alias="status"),
    origin_stream: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DeadLetterSummary]:
    """Página de dead-letters del grupo que el llamante PUEDE ver (AREA-10-08).

    El clearance se evalúa contra el radicado VIVO antes de paginar y de contar:
    la página y ``X-Total-Count`` son exactos. Si ``radicados`` no existe en el
    schema responde 503, no una lista vacía.
    """
    caller_clearance = await resolve_caller_clearance(conn, actor)
    filas, total = await DeadLetterRepository(conn).list_visible(
        request.headers.get("X-Tenant-Slug", ""),
        GRUPOS[grupo],
        caller_clearance,
        status=status_filter,
        origin_stream=origin_stream,
        offset=(page - 1) * size,
        limit=size,
    )
    response.headers["X-Total-Count"] = str(total)
    return [DeadLetterSummary.model_validate(dict(fila)) for fila in filas]


@router.get("/{grupo}/{event_id}", response_model=DeadLetterDetail)
async def get_dead_letter(
    event_id: str,
    request: Request,
    grupo: str = Depends(_grupo),
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DeadLetterDetail:
    """Detalle completo (payload + failure), tras el gate de clearance.

    Solo se audita una vista AUTORIZADA: una bloqueada responde 404 y no deja un
    rastro que confirme que el objeto clasificado existe.
    """
    caller_slug = request.headers.get("X-Tenant-Slug", "")
    caller_clearance = await resolve_caller_clearance(conn, actor)
    fila = await DeadLetterRepository(conn).get_by_event_id(
        event_id, caller_slug, GRUPOS[grupo], caller_clearance
    )
    if fila is None:
        raise _no_encontrado()
    async with conn.transaction():
        await audit.append(
            conn,
            tenant_slug=caller_slug,
            service=_SERVICE,
            action="evento.dead_letter.viewed",
            object_type="evento",
            object_ref=event_id,
            actor=str(actor),
            payload={
                "previous_status": fila["status"],
                "event_type": fila["event_type"],
                "origin_stream": fila["origin_stream"],
                "origin_group": fila["origin_group"],
            },
            canal=audit.CANAL_PETICION,
        )
    return DeadLetterDetail(
        event_id=fila["event_id"],
        event_type=fila["event_type"],
        correlation_id=fila["correlation_id"],
        tenant_slug=fila["tenant_slug"],
        origin_stream=fila["origin_stream"],
        origin_group=fila["origin_group"],
        status=fila["status"],
        dead_lettered_at=fila["dead_lettered_at"],
        resolved_at=fila["resolved_at"],
        payload=_as_dict(fila["payload"]),
        failure=_as_dict(fila["failure"]),
    )


async def _bloquear(
    conn: asyncpg.Connection, repo: DeadLetterRepository, event_id: str, slug: str, grupo: str,
    actor: uuid.UUID,
) -> asyncpg.Record:
    """``FOR UPDATE`` de una fila visible: replay y descarte no se pisan."""
    fila = await repo.get_for_update(
        event_id, slug, GRUPOS[grupo], await resolve_caller_clearance(conn, actor)
    )
    if fila is None:
        raise _no_encontrado()
    return fila


async def _cerrar(
    conn: asyncpg.Connection,
    repo: DeadLetterRepository,
    fila: asyncpg.Record,
    *,
    slug: str,
    grupo: str,
    actor: uuid.UUID,
    nuevo: str,
    accion: str,
    extra: dict[str, Any],
) -> int:
    """pending → ``nuevo`` y su asiento. 0 filas = otro administrador llegó antes (409)."""
    filas = await repo.transition_status(fila["event_id"], slug, nuevo, GRUPOS[grupo])
    if filas == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "already_resolved",
                "detail": "El evento ya no está pendiente",
                "status": 409,
            },
        )
    await audit.append(
        conn,
        tenant_slug=slug,
        service=_SERVICE,
        action=accion,
        object_type="evento",
        object_ref=fila["event_id"],
        actor=str(actor),
        payload={
            "previous_status": fila["status"],
            "new_status": nuevo,
            "event_type": fila["event_type"],
            "origin_stream": fila["origin_stream"],
            "origin_group": fila["origin_group"],
            "original_failure": _as_dict(fila["failure"]),
            **extra,
        },
        canal=audit.CANAL_PETICION,
    )
    return filas


@router.post(
    "/{grupo}/{event_id}/replay",
    response_model=DeadLetterActionResult,
    # Reprocesar o descartar es un acto de escritura: el permiso en modo lectura
    # (nivel 1) ve la cola, no la cambia.
    dependencies=[Depends(require_permission("PERM_DLQ_ADMIN", min_crud=2))],
)
async def replay_dead_letter(
    event_id: str,
    request: Request,
    grupo: str = Depends(_grupo),
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DeadLetterActionResult:
    """Reprocesa un dead-letter pendiente: núcleo del grupo + estado + asiento, atómicos."""
    replay = REPLAYS.get(grupo)
    if replay is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "replay_no_disponible",
                "detail": "Este grupo no tiene reproceso; el evento solo se puede descartar.",
                "status": 409,
            },
        )
    slug = request.headers.get("X-Tenant-Slug", "")
    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        fila = await _bloquear(conn, repo, event_id, slug, grupo, actor)
        sobre = _as_dict(fila["payload"])
        # El sobre tiene que ser de la entidad que llama (defensa adicional al WHERE).
        if sobre.get("tenant_slug", "") != slug or fila["tenant_slug"] != slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "tenant_mismatch",
                    "detail": "El evento pertenece a otra entidad",
                    "status": 422,
                },
            )
        try:
            resultado = await replay(
                conn,
                slug,
                event_id=event_id,
                event_type=fila["event_type"],
                envelope=sobre,
                actor=str(actor),
                origin_stream=fila["origin_stream"],
            )
        except HTTPException:
            raise
        except ReplayFallido as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": "replay_failed", "detail": str(exc), "status": 502},
            ) from exc
        except Exception as exc:  # noqa: BLE001 — el núcleo falló: todo revierte
            logger.exception("El reproceso del dead-letter %s (%s) falló", event_id, grupo)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "replay_failed",
                    "detail": "El reprocesamiento del evento falló; el dead-letter permanece pendiente.",
                    "status": 502,
                },
            ) from exc
        filas = await _cerrar(
            conn, repo, fila, slug=slug, grupo=grupo, actor=actor, nuevo="resolved",
            accion="evento.dead_letter.replayed",
            extra={"outcome": {"rows_affected": 1, **resultado}},
        )
    return DeadLetterActionResult(
        event_id=event_id,
        previous_status=fila["status"],
        new_status="resolved",
        outcome={"rows_affected": filas, **resultado},
    )


@router.post(
    "/{grupo}/{event_id}/discard",
    response_model=DeadLetterActionResult,
    # Reprocesar o descartar es un acto de escritura: el permiso en modo lectura
    # (nivel 1) ve la cola, no la cambia.
    dependencies=[Depends(require_permission("PERM_DLQ_ADMIN", min_crud=2))],
)
async def discard_dead_letter(
    event_id: str,
    body: DiscardRequest,
    request: Request,
    grupo: str = Depends(_grupo),
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DeadLetterActionResult:
    """Descarta deliberadamente un dead-letter pendiente, con motivo y asiento."""
    slug = request.headers.get("X-Tenant-Slug", "")
    async with conn.transaction():
        repo = DeadLetterRepository(conn)
        fila = await _bloquear(conn, repo, event_id, slug, grupo, actor)
        filas = await _cerrar(
            conn, repo, fila, slug=slug, grupo=grupo, actor=actor, nuevo="discarded",
            accion="evento.dead_letter.discarded", extra={"reason": body.reason},
        )
    return DeadLetterActionResult(
        event_id=event_id,
        previous_status=fila["status"],
        new_status="discarded",
        outcome={"rows_affected": filas},
    )
