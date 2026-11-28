# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Stream worker for notification-service (E16).

Consume dos streams del bus en PostgreSQL (`docuboto.core.bus`, ADR-049):
  - orpycamcp.workflow.events  → process_event
  - orpycamcp.document.events  → process_document_event

Dead-letter / reclamación (R-4, ADR-021 Fase 3):
  - A-1 (confirm-only-on-success): the delivery is confirmed ONLY when the
    handler returns without raising.  A failure leaves it pending; the bus's
    reclaim sweep retries it and after max_deliveries=5 parks it with a durable
    Postgres record.
  - _persist_dead_letter: atomic Postgres write (evento_dead_letter + audit_log
    in the same transaction).  Transient errors propagate — no dead-letter without
    durable trace (closes R-4).
  - R1: events without tenant_slug → immutable trace under SYSTEM_TENANT in
    public.audit_log; function returns normally so the helper can confirm.

Cadence invariant (in `docuboto.core.bus`): min idle 60 s > reclaim 30 s > wait 5 s.
"""

from __future__ import annotations

import json
import logging

from orpycamcp_common.database import acquire_tenant, get_pool

from docuboto.config import notification as settings
from docuboto.core import bus
from notification.core.constants import DLQ_GROUP
from notification.jobs import deadletter_retention, retention
from notification.repositories.dead_letter_repository import DeadLetterRepository
from notification.services.event_consumer import process_document_event, process_event

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

WORKFLOW_STREAM = "orpycamcp.workflow.events"
DOCUMENT_STREAM = "orpycamcp.document.events"
# GROUP doubles as the origin_group written to (and filtered on) the shared
# evento_dead_letter table (ADR-021 Fase 5 Inc.2) — sourced from
# app.core.constants so the worker, the admin router, and the retention job can
# never drift apart. See the module docstring in notification/core/constants.py.
GROUP = DLQ_GROUP
_SERVICE = "notification-service"

# Cadence for the daily housekeeping (dedup retention + durable DLQ retention).
# Read once at import time so tests can patch the module constant independently
# of the settings singleton.
RETENTION_INTERVAL: float = float(settings.retention_interval_seconds)


async def _handle(data: dict[str, str]) -> None:
    """Enruta por tipo de evento: los de flujo a `process_event`, el resto a
    `process_document_event` (los dos verifican la firma del sobre)."""
    tipo = data.get("event_type") or ""
    if tipo.startswith("workflow."):
        await process_event(data)
    else:
        await process_document_event(data)


async def _persist_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Durable persistence callback for dead-lettered events (R-4, ADR-021 Fase 3).

    Writes the dead-letter event atomically to ``evento_dead_letter`` (Postgres)
    and ``audit_log`` **inside a single transaction**.  The callback is passed to
    ``reclaim_stale`` as ``on_dead_letter``; under the R-4 contract the helper
    gates the confirmation on this callback's success: if this function raises, the
    delivery stays pending in for the next sweep — there is no dead-letter without a durable
    trace.

    Edge case — permanently malformed event (no ``tenant_slug``) — R1:
        Cannot be written to any tenant schema; retrying forever would create an
        infinite loop.  An immutable entry is written to ``public.audit_log`` under
        SYSTEM_TENANT so even the poison pill leaves a durable trace.  If that audit
        write fails (transient DB error), the exception propagates so the helper
        does not confirm and the delivery stays pending for the next sweep.  After
        a successful audit (or if the audit module is unavailable), returns normally
        so the helper can confirm.

    Idempotency:
        ``INSERT … ON CONFLICT (origin_group, event_id) DO NOTHING RETURNING event_id`` — a
        second call with the same ``event_id`` returns None (no row), and the
        ``audit_log`` write is skipped.  This prevents a double-trace if the insert
        succeeded but the confirmation failed (at-least-once redelivery).

    Transient errors (DB down, pool exhausted, etc.):
        Exceptions are **not caught** here.  They propagate to the helper which
        logs a warning and does not confirm — the delivery stays pending and
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
        # Return normally so the helper can confirm the poison pill — retrying an
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
                # FIX 2(b): fail-closed — without the audit module, committing
                # this INSERT would leave evento_dead_letter as the ONLY record
                # of the event, and purge_terminal would eventually delete it,
                # erasing all trace. Raise inside the open transaction so the
                # INSERT rolls back too; the exception then propagates out of
                # this callback, which under the R-4/on_dead_letter contract
                # makes the caller not confirm — the delivery stays pending
                # and is retried by the next reclaim sweep instead of being
                # silently persisted (or acked) without a durable audit trace.
                if not _AUDIT_AVAILABLE:
                    raise RuntimeError(
                        "orpycamcp_common.audit unavailable; refusing to "
                        "persist dead-letter without a durable audit trace"
                    )
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


async def _mantenimiento_diario() -> None:
    """Retención diaria: la tabla de dedup y los dead-letters terminales.

    Cada tarea con su try/except: un fallo no bloquea a la otra. La caducidad de
    los eventos del bus ya no vive aquí: la hace el barrido para todas las
    entidades (`events.purgar`, ADR-049).
    """
    try:
        await retention.run_once()
    except Exception:
        logger.warning("Fallo en job de retención de dedup", exc_info=True)
    try:
        await deadletter_retention.run_once()
    except Exception:
        logger.warning("Fallo en job de retención del dead-letter durable", exc_info=True)


async def run() -> None:
    """Bucle del grupo `notification-service` sobre el bus en PostgreSQL.

    Con el dedup insert-then-send, reintentar un evento cuyo claim ya se hizo da
    en el ON CONFLICT y se salta en silencio: seguro aun con efectos externos.
    """
    await bus.consumir(
        group=GROUP,
        streams=(WORKFLOW_STREAM, DOCUMENT_STREAM),
        handler=_handle,
        on_dead_letter=_persist_dead_letter,
        status_name="notification",
        periodico=_mantenimiento_diario,
        intervalo_periodico=RETENTION_INTERVAL,
    )
