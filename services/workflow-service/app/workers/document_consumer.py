# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Worker: consume `orpycamcp.document.events` y distribuye automáticamente (E05).

Por cada `document.radicado.created` evalúa las reglas del tenant y autoasigna
el radicado (distribution.auto_distribute). Usa el sobre canónico de
orpycamcp_common y un grupo de consumidores propio.

Dead-letter / reclamación (R-4, ADR-021 Fase 2):
  - Barrido periódico de la PEL con `reclaim_stale` (cadencia 30 s).
  - Mensajes que superan `max_deliveries=5` se mueven al dead-letter stream y
    se persisten durablemente en `evento_dead_letter` + `audit_log` en la misma
    transacción (no hay dead-letter sin traza durable).
  - R1: evento sin tenant_slug → traza bajo SYSTEM_TENANT en `public.audit_log`
    (en vez de solo loguear) para que incluso el poison pill malformado deje
    registro inmutable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import redis.exceptions as redis_exc

from orpycamcp_common import events

from app.core.database import acquire_tenant, get_pool
from app.core.redis_client import get_redis
from app.repositories.dead_letter_repository import DeadLetterRepository
from app.services.distribution import auto_distribute

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
DEADLETTER_STREAM = "orpycamcp.workflow.deadletter"
GROUP = "workflow-service"
CONSUMER = "workflow-service-1"
_SERVICE = "workflow-service"

# Cadence for the PEL reclaim sweep (seconds).
# Contract: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block timeout (5 s)
# — ensures a healthy consumer processing a message for up to 55 s is never
# pre-empted by this sweep.
RECLAIM_INTERVAL = 30.0


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    if envelope.get("event_type") != "document.radicado.created":
        return
    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}
    if not tenant_slug or not payload.get("id"):
        return
    async with acquire_tenant(tenant_slug) as conn:
        await auto_distribute(conn, tenant_slug, payload)


async def _persist_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Durable persistence callback for dead-lettered events (R-4, ADR-021 Fase 2).

    Writes the dead-letter event atomically to ``evento_dead_letter`` (Postgres)
    and ``audit_log`` **inside a single transaction**.  The callback is passed to
    ``reclaim_stale`` as ``on_dead_letter``; under the R-4 contract the helper
    gates the XACK on this callback's success: if this function raises, the message
    stays in the PEL for the next sweep — there is no dead-letter without a durable
    trace.

    Edge case — permanently malformed event (no ``tenant_slug``) — R1 improvement:
        Cannot be written to any tenant schema; retrying forever would create an
        infinite loop.  Unlike signature-service (which only logs), workflow-service
        writes an immutable entry in ``public.audit_log`` under SYSTEM_TENANT so
        even the poison pill leaves a durable trace.  If that audit write fails
        (transient DB error), the exception propagates so the helper skips the XACK
        and the message remains in the PEL for the next sweep.  After a successful
        audit (or if the audit module is unavailable), returns normally so the
        helper can XACK.

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
    """Long-running consumer loop. Cancel to shut down cleanly.

    Normal processing path (A-1 — ack-only-on-success, R-WF1 / ADR-021):
      - xack is called ONLY if _handle succeeded.  A handler failure leaves
        the message in the PEL (Pending Entries List); the reclaim sweep
        (reclaim_stale) will pick it up, retry, and after max_deliveries=5
        escalate it to the dead-letter stream with a durable Postgres record.
      - CancelledError is always re-raised so the task shuts down cleanly.
      - auto_distribute is idempotent (guard RN-8: get_next_step_number > 1
        returns early), so re-processing an already-distributed radicado is
        safe and produces no duplicate assignment.

    Reclaim sweep (dead-letter, R-4 / ADR-021 Fase 2):
      - Runs on cadence (RECLAIM_INTERVAL=30 s), separate from the normal loop.
      - Messages that exceed max_deliveries=5 are durably persisted to
        evento_dead_letter + audit_log before XACK (gated by on_dead_letter).
      - A sweep failure logs a WARNING and does not crash the loop.

    Cadence invariant: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block (5 s).
    """
    redis = get_redis()
    await events.ensure_group(redis, DOCUMENT_STREAM, GROUP)

    # Track last reclaim sweep time.  The cadence relationship to respect:
    #   min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block (5 s)
    # means stale messages are never reclaimed before a healthy consumer has
    # had at least 55 s to finish processing them.
    _last_reclaim = time.monotonic()

    while True:
        try:
            messages = await redis.xreadgroup(
                GROUP, CONSUMER, {DOCUMENT_STREAM: ">"}, count=10, block=5000
            )
        except asyncio.CancelledError:
            raise
        except (redis_exc.TimeoutError, asyncio.TimeoutError):
            # Normal: block= expired with no new entries — not an error; resume immediately.
            messages = []
        except Exception:
            logger.warning("Error en el bucle del consumidor de workflow", exc_info=True)
            await asyncio.sleep(5)
            messages = []

        for _stream, entries in messages or []:  # type: ignore[misc]
            for msg_id, data in entries:  # type: ignore[union-attr]
                try:
                    await _handle(data)
                    await redis.xack(DOCUMENT_STREAM, GROUP, msg_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A-1 (workflow, R-WF1): do NOT ack on failure.  The message
                    # stays in the PEL and reclaim_stale will retry it; after
                    # max_deliveries it escalates to the dead-letter stream with a
                    # durable Postgres record.  auto_distribute is idempotent
                    # (guard RN-8) so retrying is safe.
                    logger.warning(
                        "Fallo distribuyendo evento — permanece en PEL para reintento",
                        exc_info=True,
                    )

        # Reclaim sweep — runs on cadence, not on every iteration.
        now = time.monotonic()
        if now - _last_reclaim >= RECLAIM_INTERVAL:
            try:
                await events.reclaim_stale(
                    redis,
                    DOCUMENT_STREAM,
                    GROUP,
                    CONSUMER,
                    handler=_handle,
                    min_idle_ms=60_000,
                    max_deliveries=5,
                    deadletter_stream=DEADLETTER_STREAM,
                    on_dead_letter=_persist_dead_letter,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A sweep failure must not crash the consumer loop.
                logger.warning("Fallo en barrido de reclamación", exc_info=True)
            _last_reclaim = now
