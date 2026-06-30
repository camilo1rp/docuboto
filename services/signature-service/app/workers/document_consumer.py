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


async def _audit_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Audit callback for dead-lettered events.

    Records the parking of an unprocessable event in the immutable audit log
    to satisfy Ley 594/2000 traceability requirements: an event that could not
    be delivered must leave a permanent trace rather than disappearing silently.

    R-9: ``actor`` is set to ``"system"`` because the *worker* performs this
    audit action, not the original user.  The original actor is preserved inside
    ``payload["envelope"]["actor"]`` so the forensic record can trace who
    originally triggered the event.

    R-5/R-6: ``payload`` carries the complete original envelope (including its
    payload dict) so the immutable Postgres row is self-sufficient for forensic
    reconstruction (Ley 594/2000 art. 4 and art. 19) — the Redis DLQ is
    treated as an operational buffer, not the authoritative copy.

    The helper in events.reclaim_stale already wraps this callback in try/except
    so an error here never blocks the xack or breaks the sweep; we are still
    defensive because the outer wrapper only catches *callback* exceptions —
    errors inside acquire_tenant (e.g. invalid slug) should not propagate.
    """
    if not _AUDIT_AVAILABLE:
        return  # pragma: no cover

    tenant_slug = envelope.get("tenant_slug")
    if not tenant_slug:
        logger.warning(
            "Cannot audit dead-letter: envelope missing tenant_slug (event_id=%s)",
            envelope.get("event_id"),
        )
        return

    # Extract business identifiers from the event payload defensively —
    # they may be absent if the event was malformed.
    evt_payload: dict = envelope.get("payload") or {}
    tracking_number: str | None = evt_payload.get("tracking_number")
    doc_id: str | None = evt_payload.get("id")

    # Self-sufficient audit payload: failure details + complete original envelope
    # + business identifiers extracted for quick querying without JSON traversal.
    audit_payload = {
        "failure": failure_meta,
        "envelope": {
            "event_id": envelope.get("event_id"),
            "event_type": envelope.get("event_type"),
            "correlation_id": envelope.get("correlation_id"),
            "tenant_slug": tenant_slug,
            "occurred_at": envelope.get("occurred_at"),
            "actor": envelope.get("actor"),  # original actor preserved
            "payload": evt_payload,
        },
        "business": {
            "tracking_number": tracking_number,
            "doc_id": doc_id,
        },
    }

    try:
        async with acquire_tenant(tenant_slug) as conn:
            await _audit.append(
                conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action="evento.dead_letter",
                object_type="evento",
                object_ref=envelope.get("event_id"),
                actor="system",  # R-9: worker performs this system-level act
                canal="system",
                payload=audit_payload,
            )
    except Exception:
        logger.warning(
            "Failed to audit dead-letter for event_id=%s tenant=%s",
            envelope.get("event_id"),
            tenant_slug,
            exc_info=True,
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
                    on_dead_letter=_audit_dead_letter,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A sweep failure must not crash the consumer loop.
                logger.warning("Fallo en barrido de reclamación", exc_info=True)
            _last_reclaim = now
