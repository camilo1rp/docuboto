# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Worker: consumes ``orpycamcp.document.events`` and re-syncs firma_solicitud.nivel_seguridad.

On each ``document.radicado.reclassified`` event, updates firma_solicitud rows
whose objeto_tipo='radicado' and objeto_id matches the reclassified radicado
(by tracking_number or UUID text).  This keeps the clearance snapshot in
firma_solicitud consistent with the authoritative value in radicados without
requiring a cross-service join on every signature read.

Pattern mirrors workflow-service/app/workers/document_consumer.py.
"""

from __future__ import annotations

import asyncio
import logging
import time

import redis.exceptions as redis_exc

from orpycamcp_common import events

from app.core.database import acquire_tenant
from app.core.redis_client import get_redis
from app.repositories.cadena_repository import CadenaRepository
from app.repositories.dead_letter_repository import DeadLetterRepository

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
DEADLETTER_STREAM = "orpycamcp.signature.deadletter"
GROUP = "signature-service"
CONSUMER = "signature-service-1"
_SERVICE = "signature-service"

# Cadence for the PEL reclaim sweep (seconds).
# Contract: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block timeout (5 s)
# — ensures a healthy consumer processing a message for up to 55 s is never
# pre-empted by this sweep.
RECLAIM_INTERVAL = 30.0


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    if envelope.get("event_type") != "document.radicado.reclassified":
        return

    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}

    if not tenant_slug:
        logger.warning("Evento reclassified sin tenant_slug — ignorado")
        return

    nivel_nuevo = payload.get("nivel_nuevo")
    if nivel_nuevo is None:
        logger.warning("Evento reclassified sin nivel_nuevo — ignorado")
        return

    tracking_number: str | None = payload.get("tracking_number")
    doc_id: str | None = payload.get("id")

    # Build a deduplicated list of known identifiers for this radicado.
    objeto_ids = list({x for x in [tracking_number, doc_id] if x is not None})
    if not objeto_ids:
        logger.warning("Evento reclassified sin tracking_number ni id — ignorado")
        return

    async with acquire_tenant(tenant_slug) as conn:
        repo = CadenaRepository(conn)
        # M-2: UPDATE and audit are atomic — both commit or both roll back.
        async with conn.transaction():
            n = await repo.actualizar_nivel_seguridad("radicado", objeto_ids, int(nivel_nuevo))

            if n > 0 and _AUDIT_AVAILABLE:
                await _audit.append(
                    conn,
                    tenant_slug=tenant_slug,
                    service=_SERVICE,
                    action="firma.solicitud_reclasificada",
                    object_type="radicado",
                    object_ref=tracking_number,
                    actor=envelope.get("actor"),
                    payload={"nivel_nuevo": nivel_nuevo, "filas": n},
                )
                logger.info(
                    "Re-synced %d firma_solicitud row(s) for radicado %s → nivel %s",
                    n,
                    tracking_number,
                    nivel_nuevo,
                )


async def _persist_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Durable persistence callback for dead-lettered events (R-4, ADR-021).

    Writes the dead-letter event atomically to ``evento_dead_letter`` (Postgres)
    and ``audit_log`` **inside a single transaction**.  The callback is passed to
    ``reclaim_stale`` as ``on_dead_letter``; under the new R-4 contract the helper
    gates the XACK on this callback's success: if this function raises, the message
    stays in the PEL for the next sweep — there is no dead-letter without a durable
    trace.

    Edge case — permanently malformed event (no ``tenant_slug``):
        Cannot be written to any tenant schema; retrying forever would create an
        infinite loop.  We log the error and **return normally** so the helper can
        proceed to XACK (consuming the poison pill).  The Redis DLQ entry produced
        by the helper before calling this callback remains as the only operational
        record of this degenerate case.

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

    R-5/R-6: ``payload`` in ``audit_log`` carries the complete original envelope
    (including its business payload) so the immutable Postgres row is self-
    sufficient (Ley 594/2000 art. 4 and art. 19).
    """
    import json

    event_id: str | None = envelope.get("event_id")
    tenant_slug: str | None = envelope.get("tenant_slug")

    if not tenant_slug:
        # Permanently malformed: no tenant schema to write to.
        # Log and return so the helper can XACK the poison pill — retrying
        # an event without a tenant_slug would loop forever.
        logger.error(
            "Cannot persist dead-letter: envelope missing tenant_slug "
            "(event_id=%s) — consuming poison pill",
            event_id,
        )
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
                        actor="system",   # R-9: worker-level system act
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
    """Long-running consumer loop. Cancel to shut down cleanly."""
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
            logger.warning("Error en el bucle del consumidor de firma", exc_info=True)
            await asyncio.sleep(5)
            messages = []

        for _stream, entries in messages or []:  # type: ignore[misc]
            for msg_id, msg_data in entries:  # type: ignore[union-attr]
                try:
                    await _handle(msg_data)
                    # A-1: ack ONLY on success — a failed _handle leaves the message
                    # in the PEL (Pending Entries List) for retry on the next consumer.
                    await redis.xack(DOCUMENT_STREAM, GROUP, msg_id)
                except Exception:
                    logger.warning(
                        "Fallo procesando evento de reclasificación — "
                        "mensaje permanece en PEL para reintento",
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
