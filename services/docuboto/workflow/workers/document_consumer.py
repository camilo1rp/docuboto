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
el radicado (distribution.auto_distribute). Por cada
`document.radicado.due_date_changed` copia la fecha al `due_at` de los pasos
activos. Usa el sobre canónico de orpycamcp_common y un grupo de consumidores
propio.

El bucle es el del bus en PostgreSQL (`docuboto.core.bus`, ADR-049).

Dead-letter / reclamación (R-4, ADR-021):
  - El bus reclama lo atascado cada 30 s (`events.reclaim_stale`).
  - Lo que supera `max_deliveries=5` se persiste durablemente en
    `evento_dead_letter` + `audit_log` en la misma transacción, y solo entonces
    se confirma (no hay dead-letter sin traza durable).
  - R1: evento sin tenant_slug → traza bajo SYSTEM_TENANT en `public.audit_log`
    (en vez de solo loguear) para que incluso el poison pill malformado deje
    registro inmutable.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from datetime import time as hora

from orpycamcp_common import events
from orpycamcp_common.database import acquire_tenant, get_pool

from docuboto.config import workflow as settings
from docuboto.core import bus
from workflow.core.constants import DLQ_GROUP
from workflow.jobs import deadletter_retention
from workflow.repositories.dead_letter_repository import DeadLetterRepository
from workflow.services.distribution import auto_distribute

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
VENCIMIENTO_CAMBIADO = "document.radicado.due_date_changed"
# Sourced from workflow.core.constants (single source of truth shared with
# app/jobs/deadletter_retention.py and workflow/routers/admin_deadletter.py) — see
# the docstring there for why this can't just be a local literal.
GROUP = DLQ_GROUP
_SERVICE = "workflow-service"

#: Cadencia del mantenimiento diario (retención de dead-letters terminales).
DLQ_RETENTION_INTERVAL: float = float(settings.retention_interval_seconds)


async def apply_distribution(conn, tenant_slug: str, payload: dict) -> str | None:
    """Core de distribución automática (H3): evalúa reglas y autoasigna.

    ``conn`` debe llegar con el ``search_path`` ya fijado al tenant. Reutilizado
    por el worker (``_handle``) y por el endpoint de replay (admin_deadletter), de
    modo que el reproceso corre sobre la MISMA conexión/transacción del caller.
    ``auto_distribute`` es idempotente (guard RN-8), así que reprocesar un radicado
    ya distribuido no genera asignación duplicada.

    Devuelve la dependencia asignada, o None si no aplicaba ninguna regla / ya
    estaba distribuido.
    """
    return await auto_distribute(conn, tenant_slug, payload)


async def alinear_vencimiento(conn, payload: dict) -> None:
    """Copia el vencimiento del radicado al `due_at` de los pasos activos.

    Los pasos ya cerrados conservan el plazo con el que se tramitaron. El
    instante es el final de ese día en UTC: el radicado guarda una fecha, y el
    paso un instante.
    """
    rid = payload.get("id")
    if not rid:
        return
    crudo = payload.get("due_date")
    due = None
    if crudo:
        dia = date.fromisoformat(crudo)
        due = datetime.combine(dia, hora(23, 59), tzinfo=UTC)
    await conn.execute(
        """
        UPDATE flow_steps
           SET due_at = $2
         WHERE radicado_id = $1
           AND status IN ('pending', 'in_progress')
        """,
        rid,
        due,
    )


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    tipo = envelope.get("event_type")
    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}
    if not tenant_slug or not payload.get("id"):
        return
    if tipo == "document.radicado.created":
        async with acquire_tenant(tenant_slug) as conn:
            await apply_distribution(conn, tenant_slug, payload)
    elif tipo == VENCIMIENTO_CAMBIADO:
        async with acquire_tenant(tenant_slug) as conn:
            await alinear_vencimiento(conn, payload)


async def _persist_dead_letter(envelope: dict, failure_meta: dict) -> None:
    """Durable persistence callback for dead-lettered events (R-4, ADR-021 Fase 2).

    Writes the dead-letter event atomically to ``evento_dead_letter`` (Postgres)
    and ``audit_log`` **inside a single transaction**.  The callback is passed to
    ``reclaim_stale`` as ``on_dead_letter``; under the R-4 contract the helper
    gates the confirmation on this callback's success: if this function raises, the
    delivery stays pending for the next sweep — there is no dead-letter without a durable
    trace.

    Edge case — permanently malformed event (no ``tenant_slug``) — R1 improvement:
        Cannot be written to any tenant schema; retrying forever would create an
        infinite loop.  Unlike signature-service (which only logs), workflow-service
        writes an immutable entry in ``public.audit_log`` under SYSTEM_TENANT so
        even the poison pill leaves a durable trace.  If that audit write fails
        (transient DB error), the exception propagates so the helper does not confirm
        and the delivery stays pending for the next sweep.  After a successful
        audit (or if the audit module is unavailable), returns normally so the
        helper can confirm.

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
    """Bucle del grupo `workflow-service` sobre el bus en PostgreSQL. Cancelar para cerrar.

    Confirma solo si `_handle` tuvo éxito; un fallo deja la entrega pendiente y
    la reclamación la reintenta y, a la quinta, la aparca con `_persist_dead_letter`.
    `auto_distribute` es idempotente (guard RN-8), así que reintentar es seguro.
    """
    await bus.consumir(
        group=GROUP,
        streams=(DOCUMENT_STREAM,),
        handler=_handle,
        on_dead_letter=_persist_dead_letter,
        status_name="workflow",
        periodico=deadletter_retention.run_once,
        intervalo_periodico=DLQ_RETENTION_INTERVAL,
    )
