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

from app.core.config import settings
from app.core.constants import DLQ_GROUP
from app.core.database import acquire_tenant
from app.core.redis_client import get_redis
from app.jobs import deadletter_retention
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
# Sourced from app.core.constants (single source of truth shared with
# app/jobs/deadletter_retention.py and app/routers/admin_deadletter.py) — see
# the docstring there for why this can't just be a local literal.
GROUP = DLQ_GROUP
CONSUMER = "signature-service-1"
_SERVICE = "signature-service"

# Cadence for the PEL reclaim sweep (seconds).
# Contract: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block timeout (5 s)
# — ensures a healthy consumer processing a message for up to 55 s is never
# pre-empted by this sweep.
RECLAIM_INTERVAL = 30.0

# Cadence for the DLQ Redis buffer trim (ADR-021 Fase 4 — MVP-2).
# Read once at import time so tests can patch the module constant independently
# of the settings singleton.
DLQ_TRIM_INTERVAL: float = float(settings.retention_interval_seconds)


async def apply_reclassification(
    conn,
    tenant_slug: str,
    payload: dict,
    *,
    actor: str | None = None,
    replayed: bool = False,
) -> int:
    """Core de reclasificación (H3): re-sincroniza firma_solicitud.nivel_seguridad.

    ``conn`` debe llegar con el ``search_path`` ya fijado al tenant y, para la
    atomicidad UPDATE+audit, dentro de una transacción abierta por el caller.
    Reutilizado por el worker (``_handle``) y por el endpoint de replay
    (admin_deadletter) — el replay marca ``replayed=True`` y atribuye el acto al
    admin que lo dispara (``actor``).

    Devuelve el número de filas actualizadas (0 = no-op idempotente o payload
    incompleto).
    """
    nivel_nuevo = payload.get("nivel_nuevo")
    if nivel_nuevo is None:
        return 0

    tracking_number: str | None = payload.get("tracking_number")
    doc_id: str | None = payload.get("id")

    # Build a deduplicated list of known identifiers for this radicado.
    objeto_ids = list({x for x in [tracking_number, doc_id] if x is not None})
    if not objeto_ids:
        return 0

    repo = CadenaRepository(conn)
    n = await repo.actualizar_nivel_seguridad("radicado", objeto_ids, int(nivel_nuevo))

    if n > 0 and _AUDIT_AVAILABLE:
        audit_payload: dict = {"nivel_nuevo": nivel_nuevo, "filas": n}
        if replayed:
            audit_payload["replayed"] = True
        await _audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action="firma.solicitud_reclasificada",
            object_type="radicado",
            object_ref=tracking_number,
            actor=actor,
            payload=audit_payload,
        )
        logger.info(
            "Re-synced %d firma_solicitud row(s) for radicado %s → nivel %s",
            n,
            tracking_number,
            nivel_nuevo,
        )
    return n


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    if envelope.get("event_type") != "document.radicado.reclassified":
        return

    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}

    if not tenant_slug:
        logger.warning("Evento reclassified sin tenant_slug — ignorado")
        return

    if payload.get("nivel_nuevo") is None:
        logger.warning("Evento reclassified sin nivel_nuevo — ignorado")
        return

    if not payload.get("tracking_number") and not payload.get("id"):
        logger.warning("Evento reclassified sin tracking_number ni id — ignorado")
        return

    async with acquire_tenant(tenant_slug) as conn:
        # M-2: UPDATE and audit are atomic — both commit or both roll back.
        async with conn.transaction():
            await apply_reclassification(
                conn,
                tenant_slug,
                payload,
                actor=envelope.get("actor"),
                replayed=False,
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

    # Track cadence for both the PEL reclaim sweep and the DLQ buffer trim.
    # A single time.monotonic() call initialises both: _last_reclaim = _last_dlq_trim = t0.
    # Reclaim fires every RECLAIM_INTERVAL (30 s); DLQ trim fires every DLQ_TRIM_INTERVAL
    # (default 86 400 s / 24 h).  Deferring the first DLQ trim avoids hitting Redis before
    # the connection is warm; the daily window is negligible vs. the 30-day horizon.
    _last_reclaim = _last_dlq_trim = time.monotonic()

    while True:
        try:
            messages = await redis.xreadgroup(
                GROUP, CONSUMER, {DOCUMENT_STREAM: ">"}, count=10, block=5000
            )
        except asyncio.CancelledError:
            raise
        except (TimeoutError, redis_exc.TimeoutError):
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

        # Cadence gates — both share the same `now` snapshot (one monotonic call).
        now = time.monotonic()

        # PEL reclaim sweep: runs every RECLAIM_INTERVAL (30 s).
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

        # DLQ buffer trim: runs every DLQ_TRIM_INTERVAL (default 86 400 s / 24 h).
        # Safe: authoritative copy is in Postgres; entries older than the horizon
        # are guaranteed already mirrored (ADR-021 Fase 4 — MVP-2).
        if now - _last_dlq_trim >= DLQ_TRIM_INTERVAL:
            try:
                await events.trim_deadletter(
                    redis,
                    DEADLETTER_STREAM,
                    max_age_ms=settings.retention_dlq_days * 86_400_000,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Fallo en trim del DLQ de firma", exc_info=True)
            # Purge terminal Postgres dead-letters on the same daily cadence as the
            # Redis trim (ADR-021 Fase 4 — MVP-3).  Shares the gate to avoid a second
            # monotonic variable; both are daily housekeeping with no strict ordering
            # requirement between them.  A failure must not crash the loop or block the
            # gate (the next daily cycle will retry).
            try:
                await deadletter_retention.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Fallo en retención de dead-letters de firma", exc_info=True
                )
            _last_dlq_trim = now
