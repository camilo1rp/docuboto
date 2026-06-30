# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Stream worker for notification-service (E16).

Consumes two Redis Streams in a single XREADGROUP call:
  - orpycamcp.workflow.events  → process_event
  - orpycamcp.document.events  → process_document_event

Dead-letter / reclamación (R-4, ADR-021 Fase 3):
  - A-1 (ack-only-on-success): XACK is called ONLY when the handler returns
    without raising.  A handler failure leaves the message in the PEL; the
    reclaim sweep (reclaim_stale) picks it up, retries, and after max_deliveries=5
    escalates it to the dead-letter stream with a durable Postgres record.
  - Periodic PEL reclaim sweep (cadence 30 s) called once per stream.
  - _persist_dead_letter: atomic Postgres write (evento_dead_letter + audit_log
    in the same transaction).  Transient errors propagate — no dead-letter without
    durable trace (closes R-4).
  - R1: events without tenant_slug → immutable trace under SYSTEM_TENANT in
    public.audit_log; function returns normally so the helper can XACK.

Cadence invariant:
  min_idle_ms (60 s) > RECLAIM_INTERVAL (30 s) > block timeout (5 s)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import redis.exceptions as redis_exc

from orpycamcp_common import events

from app.core.config import settings
from app.core.database import acquire_tenant, get_pool
from app.core.redis_client import get_redis
from app.jobs import retention
from app.repositories.dead_letter_repository import DeadLetterRepository
from app.services.event_consumer import process_document_event, process_event

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

WORKFLOW_STREAM = "orpycamcp.workflow.events"
DOCUMENT_STREAM = "orpycamcp.document.events"
DEADLETTER_STREAM = "orpycamcp.notification.deadletter"
GROUP = "notification-service"
CONSUMER = "notification-service-1"
_SERVICE = "notification-service"

# Cadence for the PEL reclaim sweep (seconds).
# Contract: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block timeout (5 s)
# — ensures a healthy consumer processing a message for up to 55 s is never
# pre-empted by this sweep.
RECLAIM_INTERVAL = 30.0

# Cadence for the dedup retention job (ADR-021 Fase 4 — MVP-1).
# Read once at import time so tests can patch the module constant independently
# of the settings singleton.
RETENTION_INTERVAL: float = float(settings.retention_interval_seconds)

# Each stream routes to its own handler.  The workflow stream uses the legacy
# flat format; the document stream uses the canonical orpycamcp_common envelope.
_HANDLERS: dict[str, Callable[[dict], Awaitable[None]]] = {
    WORKFLOW_STREAM: process_event,
    DOCUMENT_STREAM: process_document_event,
}


def _name(stream: object) -> str:
    return stream.decode() if isinstance(stream, bytes | bytearray) else str(stream)


async def _persist_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Durable persistence callback for dead-lettered events (R-4, ADR-021 Fase 3).

    Writes the dead-letter event atomically to ``evento_dead_letter`` (Postgres)
    and ``audit_log`` **inside a single transaction**.  The callback is passed to
    ``reclaim_stale`` as ``on_dead_letter``; under the R-4 contract the helper
    gates the XACK on this callback's success: if this function raises, the message
    stays in the PEL for the next sweep — there is no dead-letter without a durable
    trace.

    Edge case — permanently malformed event (no ``tenant_slug``) — R1:
        Cannot be written to any tenant schema; retrying forever would create an
        infinite loop.  An immutable entry is written to ``public.audit_log`` under
        SYSTEM_TENANT so even the poison pill leaves a durable trace.  If that audit
        write fails (transient DB error), the exception propagates so the helper
        skips the XACK and the message remains in the PEL for the next sweep.  After
        a successful audit (or if the audit module is unavailable), returns normally
        so the helper can XACK.

    Idempotency:
        ``INSERT … ON CONFLICT (event_id) DO NOTHING RETURNING event_id`` — a
        second call with the same ``event_id`` returns None (no row), and the
        ``audit_log`` write is skipped.  This prevents a double-trace if XADD
        succeeded but XACK failed (at-least-once redelivery).

    Transient errors (DB down, pool exhausted, etc.):
        Exceptions are **not caught** here.  They propagate to the helper which
        logs a warning and skips the XACK — the message remains in the PEL and
        will be retried on the next reclaim sweep.  This is what closes R-4.

    R-9: ``actor`` is ``"system"`` — the worker performs this act, not the
    original user.  The original actor is preserved in
    ``payload["envelope"]["actor"]`` for forensic reconstruction.
    """
    event_id: str | None = envelope.get("event_id")
    tenant_slug: str | None = envelope.get("tenant_slug")

    if not tenant_slug:
        # R1: permanently malformed event — write an immutable trace under
        # SYSTEM_TENANT in public.audit_log so the poison pill is not silently
        # consumed.  Transient errors propagate (no ack without durable trace).
        logger.error(
            "Cannot persist dead-letter: envelope missing tenant_slug "
            "(event_id=%s) — auditing under SYSTEM_TENANT and consuming poison pill",
            event_id,
        )
        if _AUDIT_AVAILABLE:
            async with get_pool().acquire() as conn:
                await _audit.append(
                    conn,
                    tenant_slug=None,  # → SYSTEM_TENANT (__system__)
                    service=_SERVICE,
                    action="evento.dead_letter",
                    object_type="evento",
                    object_ref=event_id,
                    actor="system",
                    canal="system",
                    payload={
                        "failure": failure_meta,
                        "envelope": envelope,
                        "note": "evento sin tenant_slug; registrado bajo SYSTEM_TENANT",
                    },
                )
        # Return normally so the helper can XACK the poison pill — retrying an
        # event without tenant_slug would loop forever (no tenant schema to write to).
        return

    # Extract business identifiers defensively; may be absent in malformed events.
    evt_payload: dict = envelope.get("payload") or {}
    tracking_number: str | None = evt_payload.get("tracking_number")
    doc_id: str | None = evt_payload.get("id")

    # Self-sufficient audit payload: failure + complete original envelope
    # + business identifiers for fast querying without JSON traversal.
    audit_payload = {
        "failure": failure_meta,
        "envelope": {
            "event_id": event_id,
            "event_type": envelope.get("event_type"),
            "correlation_id": envelope.get("correlation_id"),
            "tenant_slug": tenant_slug,
            "occurred_at": envelope.get("occurred_at"),
            "actor": envelope.get("actor"),  # original actor preserved (R-9)
            "payload": evt_payload,
        },
        "business": {
            "tracking_number": tracking_number,
            "doc_id": doc_id,
        },
    }

    # Transient errors propagate — do NOT catch here (R-4 contract).
    async with acquire_tenant(tenant_slug) as conn:
        repo = DeadLetterRepository(conn)
        async with conn.transaction():
            row = await repo.insert_if_new(
                event_id=event_id or "",
                event_type=envelope.get("event_type"),
                correlation_id=envelope.get("correlation_id"),
                tenant_slug=tenant_slug,
                # Serialize the whole envelope as the canonical payload record.
                payload_json=json.dumps(audit_payload["envelope"], default=str),
                failure_json=json.dumps(failure_meta, default=str),
                origin_stream=failure_meta.get("dead_letter_origin_stream"),
                origin_group=failure_meta.get("dead_letter_group"),
            )
            if row is not None:
                # INSERT was real — write the immutable audit trace in the same tx.
                if _AUDIT_AVAILABLE:
                    await _audit.append(
                        conn,
                        tenant_slug=tenant_slug,
                        service=_SERVICE,
                        action="evento.dead_letter",
                        object_type="evento",
                        object_ref=event_id,
                        actor="system",  # R-9: worker-level system act
                        canal="system",
                        payload=audit_payload,
                    )
                logger.info(
                    "Dead-letter persisted: event_id=%s tenant=%s type=%s",
                    event_id,
                    tenant_slug,
                    envelope.get("event_type"),
                )
            else:
                # ON CONFLICT — already durably stored; idempotent no-op.
                logger.debug(
                    "Dead-letter already persisted (idempotent): event_id=%s tenant=%s",
                    event_id,
                    tenant_slug,
                )


async def run() -> None:
    """Long-running consumer loop.  Cancel to shut down cleanly.

    Normal processing path (A-1 — ack-only-on-success, ADR-021 Fase 3):
      - XACK is called ONLY if the handler returned without raising.  A handler
        failure (ValueError for missing event_id/tenant_slug; BD error in claim;
        unhandled bug) leaves the message in the PEL (Pending Entries List).
      - The reclaim sweep (reclaim_stale) picks up stale PEL entries, retries,
        and after max_deliveries=5 escalates them to the dead-letter stream with
        a durable Postgres record via _persist_dead_letter.
      - CancelledError is always re-raised so the task shuts down cleanly.
      - With insert-then-send dedup in the handlers, reprocesing a message that
        was already claimed is safe: the dedup guard returns early without
        re-triggering external effects.

    Reclaim sweep (dead-letter, R-4 / ADR-021 Fase 3):
      - Runs on cadence (RECLAIM_INTERVAL=30 s), called once per stream.
      - A sweep failure logs a WARNING and does not crash the loop.

    Cadence invariant: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block (5 s).
    """
    redis = get_redis()

    for stream in _HANDLERS:
        await events.ensure_group(redis, stream, GROUP)

    read_spec = {stream: ">" for stream in _HANDLERS}

    # Track cadence for both the PEL reclaim sweep and the retention job.
    # A single time.monotonic() call initialises both: _last_reclaim = _last_retention = t0.
    # This preserves the exact monotonic call count that existing tests rely on
    # (one call before the loop, one inside per iteration).
    # Retention first fires after RETENTION_INTERVAL seconds (≈24 h).  Deferring
    # the first run avoids hitting the DB before the pool is warm; the daily window
    # is negligible compared to the 7-day TTL.
    _last_reclaim = _last_retention = time.monotonic()

    while True:
        try:
            messages = await redis.xreadgroup(GROUP, CONSUMER, read_spec, count=10, block=5000)
        except asyncio.CancelledError:
            raise
        except (redis_exc.TimeoutError, asyncio.TimeoutError):
            # Normal: block= expired with no new entries — not an error; resume immediately.
            messages = []
        except Exception:
            logger.warning("Error en el bucle del worker de notificaciones", exc_info=True)
            await asyncio.sleep(5)
            messages = []

        for stream, entries in messages or []:  # type: ignore[misc]
            stream_name = _name(stream)
            handler = _HANDLERS.get(stream_name)
            for msg_id, data in entries:  # type: ignore[union-attr]
                try:
                    if handler is not None:
                        await handler(data)
                    # A-1: ack ONLY on success.  Reaches here only when handler
                    # returned without raising (or when there is no handler for
                    # the stream — unknown entries are acked to clear the PEL).
                    await redis.xack(stream_name, GROUP, msg_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A-1: do NOT ack on failure.  The message stays in the PEL
                    # and reclaim_stale will retry it; after max_deliveries it
                    # escalates to the dead-letter stream with a durable record.
                    # With insert-then-send dedup, retrying a message whose claim
                    # already succeeded will hit the ON CONFLICT guard and skip
                    # silently — safe even for non-idempotent effects.
                    logger.warning(
                        "Fallo procesando evento de notificación (stream=%s) — "
                        "mensaje permanece en PEL para reintento",
                        stream_name,
                        exc_info=True,
                    )

        # Cadence gates — both share the same `now` snapshot (one monotonic call).
        now = time.monotonic()

        # PEL reclaim sweep: runs every RECLAIM_INTERVAL (30 s).
        # Called once per stream so each PEL is inspected independently.
        if now - _last_reclaim >= RECLAIM_INTERVAL:
            for stream_name, handler in _HANDLERS.items():
                try:
                    await events.reclaim_stale(
                        redis,
                        stream_name,
                        GROUP,
                        CONSUMER,
                        handler=handler,
                        min_idle_ms=60_000,
                        max_deliveries=5,
                        deadletter_stream=DEADLETTER_STREAM,
                        on_dead_letter=_persist_dead_letter,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A sweep failure must not crash the consumer loop.
                    logger.warning(
                        "Fallo en barrido de reclamación (stream=%s)", stream_name, exc_info=True
                    )
            _last_reclaim = now

        # Dedup retention job: runs every RETENTION_INTERVAL (default 86 400 s / 24 h).
        # ADR-021 Fase 4 — MVP-1: purges notification_processed_events per tenant.
        # A failure is logged as WARNING; it does NOT crash the consumer loop.
        if now - _last_retention >= RETENTION_INTERVAL:
            try:
                await retention.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Fallo en job de retención de dedup", exc_info=True)
            _last_retention = now
