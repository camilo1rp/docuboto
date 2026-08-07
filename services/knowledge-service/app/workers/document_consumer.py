# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Worker: consume `orpycamcp.document.events` y auto-puebla el índice pgvector (E21).

Por cada `document.radicado.created` embebe el texto derivado (subject/remitente) y
lo indexa respetando ACL/RF-SEG-08 (fail-closed, ver `app/services/ingestion.py`).
Por cada `document.radicado.reclassified` actualiza o purga el chunk indexado. Por
cada `document.radicado.annulled` purga PERMANENTEMENTE el chunk indexado
(residual "purga-en-anulación", `ingestion.void_radicado`) — a diferencia de
`reclassified`, esta purga no es reversible: el radicado anulado no se des-anula.
Usa el sobre canónico de `orpycamcp_common` y un grupo de consumidores PROPIO
("knowledge-ingest") — Redis Streams entrega una copia independiente del stream a
cada consumer-group, así que este worker no compite con el de workflow-service
(auto-distribución) sobre el mismo stream.

Es un espejo deliberado de
``workflow-service/app/workers/document_consumer.py``: mismo patrón de bucle
(ack-only-on-success), mismo barrido de reclamación (`reclaim_stale`) y mismo
contrato de persistencia durable del dead-letter (R-4, ADR-021 Fase 2) — sin la
tarea de trim/retención periódica del DLQ, que queda fuera de este MVP.

  - Barrido periódico de la PEL con `reclaim_stale` (cadencia 30 s).
  - Mensajes que superan `max_deliveries=5` se mueven al dead-letter stream y se
    persisten durablemente en `evento_dead_letter` + `audit_log` en la misma
    transacción (no hay dead-letter sin traza durable).
  - Evento sin `tenant_slug` → traza bajo SYSTEM_TENANT en `public.audit_log` (en
    vez de solo loguear) para que incluso el poison pill malformado deje registro
    inmutable.
  - Evento sin `tenant_slug` O sin `tracking_number` (`_handle`, camino de entrada
    ANTES de tocar BD de negocio) → traza `knowledge.ingest_malformed_event`
    (`_trace_malformed_event`, bajo SYSTEM_TENANT si falta `tenant_slug`, bajo el
    tenant si solo falta `tracking_number`) antes del `return`/XACK. Corrección
    tenant-security-auditor: el `return` silencioso original nunca entraba al
    camino de fallo/PEL/DLQ (se consideraba éxito y se ackeaba de inmediato), así
    que el poison pill se perdía sin dejar traza — contradecía el contrato de
    arriba. Best-effort: un fallo AL AUDITAR aquí no rompe el bucle del consumer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import redis.exceptions as redis_exc
from orpycamcp_common import events

from app.core.config import settings
from app.core.database import acquire_tenant, get_pool
from app.core.redis_client import get_redis
from app.repositories.dead_letter_repository import DeadLetterRepository
from app.services import ingestion

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
DEADLETTER_STREAM = "orpycamcp.knowledge.deadletter"
GROUP = settings.knowledge_ingest_group
CONSUMER = "knowledge-service-1"
_SERVICE = "knowledge-service"

_HANDLED_EVENT_TYPES = (
    "document.radicado.created",
    "document.radicado.reclassified",
    "document.radicado.annulled",
)

# Cadencia del barrido de reclamación de la PEL (segundos).
# Contrato: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block timeout (5 s) —
# garantiza que un consumidor sano procesando un mensaje hasta 55 s no sea
# desalojado por este barrido.
RECLAIM_INTERVAL = 30.0


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    event_type = envelope.get("event_type")
    if event_type not in _HANDLED_EVENT_TYPES:
        return

    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}
    tracking_number = payload.get("tracking_number")
    if not tenant_slug or not tracking_number:
        await _trace_malformed_event(
            envelope, tenant_slug=tenant_slug, tracking_number=tracking_number
        )
        return

    async with acquire_tenant(tenant_slug) as conn:
        if event_type == "document.radicado.created":
            await ingestion.ingest_radicado_created(
                conn, tenant_slug=tenant_slug, payload=payload
            )
        elif event_type == "document.radicado.reclassified":
            await ingestion.apply_reclassification(
                conn, tenant_slug=tenant_slug, payload=payload
            )
        else:  # "document.radicado.annulled"
            await ingestion.void_radicado(conn, tenant_slug=tenant_slug, payload=payload)


async def _trace_malformed_event(
    envelope: dict, *, tenant_slug: str | None, tracking_number: str | None
) -> None:
    """Deja traza inmutable de un evento `created`/`reclassified` malformado
    (falta `tenant_slug` y/o `tracking_number`) ANTES de que `_handle` retorne y
    `run()` haga XACK — ver el bullet correspondiente en el docstring del módulo.

    - Sin `tenant_slug`: no hay schema de tenant al que escribir → traza bajo
      SYSTEM_TENANT en `public.audit_log` (misma convención que
      `_persist_dead_letter`).
    - Con `tenant_slug` pero sin `tracking_number`: traza bajo ese tenant.

    Best-effort: envuelto en try/except a propósito. Un evento malformado no se
    arregla reintentándolo — el payload seguirá sin tenant/tracking_number en
    cada redelivery — así que un fallo AL AUDITAR (p. ej. DB transitoriamente
    caída) no debe bloquear el bucle del consumer ni dejar el mensaje reintentando
    para siempre en la PEL; se loguea y el mensaje igual se considera manejado.
    """
    if not _AUDIT_AVAILABLE:
        return
    audit_payload = {
        "event_type": envelope.get("event_type"),
        "event_id": envelope.get("event_id"),
        "tenant_slug": tenant_slug,
        "tracking_number": tracking_number,
        "payload": envelope.get("payload"),
    }
    try:
        if not tenant_slug:
            async with get_pool().acquire() as conn:
                await _audit.append(
                    conn,
                    tenant_slug=None,  # → SYSTEM_TENANT (__system__)
                    service=_SERVICE,
                    action="knowledge.ingest_malformed_event",
                    object_type="evento",
                    object_ref=envelope.get("event_id"),
                    actor="system",
                    canal="system",
                    payload=audit_payload,
                )
        else:
            async with acquire_tenant(tenant_slug) as conn:
                await _audit.append(
                    conn,
                    tenant_slug=tenant_slug,
                    service=_SERVICE,
                    action="knowledge.ingest_malformed_event",
                    object_type="evento",
                    object_ref=envelope.get("event_id"),
                    actor="system",
                    canal="system",
                    payload=audit_payload,
                )
    except Exception:
        logger.warning(
            "Fallo dejando traza de evento malformado (best-effort) — el mensaje "
            "igual se considera manejado (XACK)",
            exc_info=True,
        )


async def _persist_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Durable persistence callback for dead-lettered events (ADR-021 Fase 2).

    Writes the dead-letter event atomically to ``evento_dead_letter`` (Postgres)
    and ``audit_log`` **inside a single transaction**. The callback is passed to
    ``reclaim_stale`` as ``on_dead_letter``; the helper gates the XACK on this
    callback's success: if this function raises, the message stays in the PEL for
    the next sweep — there is no dead-letter without a durable trace.

    Edge case — permanently malformed event (no ``tenant_slug``):
        Cannot be written to any tenant schema; retrying forever would create an
        infinite loop. Writes an immutable entry in ``public.audit_log`` under
        SYSTEM_TENANT so even the poison pill leaves a durable trace. If that audit
        write fails (transient DB error), the exception propagates so the helper
        skips the XACK and the message remains in the PEL for the next sweep.

    Idempotency:
        ``INSERT … ON CONFLICT (event_id) DO NOTHING RETURNING event_id`` — a
        second call with the same ``event_id`` returns None (no row), and the
        ``audit_log`` write is skipped.

    Transient errors (DB down, pool exhausted, etc.):
        Exceptions are **not caught** here. They propagate to the helper which
        logs a warning and skips the XACK — the message remains in the PEL and
        will be retried on the next reclaim sweep.
    """
    event_id: str | None = envelope.get("event_id")
    tenant_slug: str | None = envelope.get("tenant_slug")

    if not tenant_slug:
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
        return

    evt_payload: dict = envelope.get("payload") or {}
    tracking_number: str | None = evt_payload.get("tracking_number")

    audit_payload = {
        "failure": failure_meta,
        "envelope": {
            "event_id": event_id,
            "event_type": envelope.get("event_type"),
            "correlation_id": envelope.get("correlation_id"),
            "tenant_slug": tenant_slug,
            "occurred_at": envelope.get("occurred_at"),
            "actor": envelope.get("actor"),
            "payload": evt_payload,
        },
        "business": {
            "tracking_number": tracking_number,
        },
    }

    async with acquire_tenant(tenant_slug) as conn:
        repo = DeadLetterRepository(conn)
        async with conn.transaction():
            row = await repo.insert_if_new(
                event_id=event_id or "",
                event_type=envelope.get("event_type"),
                correlation_id=envelope.get("correlation_id"),
                tenant_slug=tenant_slug,
                payload_json=json.dumps(audit_payload["envelope"], default=str),
                failure_json=json.dumps(failure_meta, default=str),
                origin_stream=failure_meta.get("dead_letter_origin_stream"),
                origin_group=failure_meta.get("dead_letter_group"),
            )
            if row is not None:
                if _AUDIT_AVAILABLE:
                    await _audit.append(
                        conn,
                        tenant_slug=tenant_slug,
                        service=_SERVICE,
                        action="evento.dead_letter",
                        object_type="evento",
                        object_ref=event_id,
                        actor="system",
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
                logger.debug(
                    "Dead-letter already persisted (idempotent): event_id=%s tenant=%s",
                    event_id,
                    tenant_slug,
                )


async def run() -> None:
    """Long-running consumer loop. Cancel to shut down cleanly.

    Normal processing path (ack-only-on-success, ADR-021):
      - xack is called ONLY if _handle succeeded. A handler failure leaves the
        message in the PEL; the reclaim sweep (reclaim_stale) will pick it up,
        retry, and after max_deliveries=5 escalate it to the dead-letter stream
        with a durable Postgres record.
      - CancelledError is always re-raised so the task shuts down cleanly.
      - upsert_chunk/update_nivel_by_source/delete_by_source are idempotent, so
        re-processing an already-ingested radicado is safe.

    Reclaim sweep (dead-letter, ADR-021 Fase 2):
      - Runs on cadence (RECLAIM_INTERVAL=30 s), separate from the normal loop.
      - Messages that exceed max_deliveries=5 are durably persisted to
        evento_dead_letter + audit_log before XACK (gated by on_dead_letter).
      - A sweep failure logs a WARNING and does not crash the loop.

    Cadence invariant: min_idle (60 s) > RECLAIM_INTERVAL (30 s) > block (5 s).
    """
    redis = get_redis()
    await events.ensure_group(redis, DOCUMENT_STREAM, GROUP)

    _last_reclaim = time.monotonic()

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
            logger.warning(
                "Error en el bucle del consumidor de ingesta de knowledge-service",
                exc_info=True,
            )
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
                    # ack-only-on-success: the message stays in the PEL and
                    # reclaim_stale will retry it; after max_deliveries it
                    # escalates to the dead-letter stream with a durable record.
                    logger.warning(
                        "Fallo ingiriendo evento — permanece en PEL para reintento",
                        exc_info=True,
                    )

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
                logger.warning(
                    "Fallo en barrido de reclamación de knowledge-service", exc_info=True
                )
            _last_reclaim = now
