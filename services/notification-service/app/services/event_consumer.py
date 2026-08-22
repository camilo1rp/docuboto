# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Event consumers for notification-service (E16).

Both handlers implement the insert-then-send idempotency pattern (ADR-021 Fase 3):

  1. Parse the canonical envelope.
  2. Resolve event_id + tenant_slug; raise ValueError if either is absent — the
     message stays in the PEL and the reclaim sweep routes it to dead-letter
     (R1 in _persist_dead_letter handles the no-tenant_slug case).
  3. Claim event_id in ``notification_processed_events`` via an atomic INSERT.
     • BD failure propagates (not caught) → A-1 no-ack → PEL → reclaim sweep.
  4. If already claimed (ON CONFLICT) → return silently (dedup skip, not loss).
  5. Only after a successful claim: dispatch webhooks (E11) + send notification(s)
     built from the envelope by the pure builders below.

Trade-off: if the worker dies between step 3 (claim) and step 5 (send), that
email is permanently lost.  Accepted: email is not the legal record; audit_log
and the radicado in document-service are the authoritative sources.

External effects after the claim (webhooks, email) are best-effort: any failure
is already handled by their own retry mechanisms.  They must NOT raise exceptions
that would propagate to stream_worker and trigger a no-ack for an event that has
already been claimed — that would cause infinite PEL churn (the dedup would block
reprocessing anyway).

Consequence for the dead-letter admin replay (ADR-021 Fase 5 Inc.2): because
dispatch_webhooks and send_notification never raise, a message that made it past
the claim step (3) essentially never fails afterwards — so in practice almost
every dead-lettered notification event never reached the claim at all (the claim
itself raised, e.g. a transient DB error, and the message stayed in the PEL until
max_deliveries). The DLQ replay endpoint (app/routers/admin_deadletter.py) still
clears any stale claim defensively via ProcessedEventsRepository.delete_one before
resending, in case this invariant changes in the future.

``build_workflow_notifications`` and ``build_document_notifications`` are PURE
(no DB, no SMTP, no side effects): they map an envelope to zero or more
``NotificationSend`` objects. Both the live consumer below and the DLQ replay
endpoint call the SAME builder for a given origin stream, so the two paths can
never diverge on what gets sent (mirrors signature-service's shared
``apply_reclassification`` handler-core).
"""

from __future__ import annotations

import logging

from orpycamcp_common import events

from app.core.database import acquire_tenant
from app.repositories.processed_events_repository import ProcessedEventsRepository
from app.schemas.notification import NotificationSend
from app.services.buzones import buzon_de_codigo_o_ventanilla, resolver_buzon_dependencia
from app.services.notification_service import send_notification
from app.services.webhook_service import dispatch as dispatch_webhooks

logger = logging.getLogger(__name__)


def build_workflow_notifications(
    envelope: dict, *, buzon_destino: str
) -> list[NotificationSend]:
    """Pure builder: maps a workflow-stream envelope to its NotificationSend(s).

    Mirrors the routing rules previously inlined in ``process_event``:
      - ``workflow.step.created`` with ``payload.action == "assign"`` → assignment email.
      - ``workflow.step.overdue`` → overdue-alert email.
      - anything else → no email (empty list); webhooks still fire independently
        in the caller, not here.

    `buzon_destino` llega YA RESUELTO (ver `app/services/buzones.py`) para que
    este constructor siga siendo puro. Es **keyword-only y sin valor por
    defecto a propósito**: la versión anterior interpolaba `to_dept` —el
    NOMBRE de la dependencia, texto libre con espacios y tildes— en la parte
    local de la dirección, y fallaba el 100% de los envíos. Un default aquí
    dejaría a cualquier llamante conservando ese defecto en silencio.
    """
    tenant_slug = envelope.get("tenant_slug")
    event_type = envelope.get("event_type")
    payload_data = envelope.get("payload") or {}

    if event_type == "workflow.step.created" and payload_data.get("action") == "assign":
        tracking_number = payload_data.get("tracking_number", "N/A")
        to_dept = payload_data.get("to_dept", "unknown")
        return [
            NotificationSend(
                recipient_email=buzon_destino,
                subject=f"[OrpycaMCP] Nuevo radicado asignado: {tracking_number}",
                body=(
                    f"Se ha asignado un nuevo radicado a su dependencia.\n\n"
                    f"Número de radicado: {tracking_number}\n"
                    f"Dependencia destino: {to_dept}\n"
                    f"Inquilino: {tenant_slug}\n"
                ),
                tenant_slug=tenant_slug,
            )
        ]

    if event_type == "workflow.step.overdue":
        tracking_number = payload_data.get("tracking_number", "N/A")
        to_dept = payload_data.get("to_dept", "unknown")
        due_at = payload_data.get("due_at")
        return [
            NotificationSend(
                recipient_email=buzon_destino,
                subject=f"[OrpycaMCP] Trámite VENCIDO: {tracking_number}",
                body=(
                    f"El trámite del radicado {tracking_number} está vencido.\n\n"
                    f"Dependencia responsable: {to_dept}\n"
                    f"Fecha límite: {due_at or 'N/A'}\n"
                    f"Inquilino: {tenant_slug}\n"
                ),
                tenant_slug=tenant_slug,
            )
        ]

    return []


def build_document_notifications(envelope: dict) -> list[NotificationSend]:
    """Pure builder: maps a document-stream envelope to its NotificationSend(s).

    Mirrors the routing rules previously inlined in ``process_document_event``:
    only ``document.radicado.created`` produces an email; any other event_type
    returns an empty list (webhooks still fire independently in the caller).
    """
    tenant_slug = envelope.get("tenant_slug")
    if envelope.get("event_type") != "document.radicado.created":
        return []

    payload_data = envelope.get("payload") or {}
    tracking_number = payload_data.get("tracking_number", "N/A")
    dest_dept_code = payload_data.get("dest_dept_code")

    recipient = buzon_de_codigo_o_ventanilla(dest_dept_code)

    return [
        NotificationSend(
            recipient_email=recipient,
            subject=f"[OrpycaMCP] Documento radicado: {tracking_number}",
            body=(
                f"Se ha radicado un nuevo documento.\n\n"
                f"Número de radicado: {tracking_number}\n"
                f"Dependencia destino: "
                f"{dest_dept_code if dest_dept_code is not None else 'sin asignar'}\n"
                f"Inquilino: {tenant_slug}\n"
            ),
            tenant_slug=tenant_slug,
        )
    ]


async def process_event(data: dict[str, str]) -> None:
    """Consumidor del stream de workflow (orpycamcp.workflow.events).

    Reacciona a ``workflow.step.created`` (asignación) y ``workflow.step.overdue``
    (alerta de vencimiento) notificando a la dependencia responsable.
    """
    envelope = events.parse_fields(data)
    event_id: str | None = envelope.get("event_id")
    tenant_slug: str | None = envelope.get("tenant_slug")

    # Events without event_id or tenant_slug cannot be deduplicated safely:
    # raise so the message stays in the PEL → reclaim_stale → R1 in
    # _persist_dead_letter handles the no-tenant_slug case under SYSTEM_TENANT.
    if not event_id or not tenant_slug:
        raise ValueError(
            "workflow event missing event_id or tenant_slug "
            f"(event_id={event_id!r}, tenant_slug={tenant_slug!r})"
        )

    # Claim the event_id before any external effect (insert-then-send).
    # BD failures propagate → A-1 no-ack → the message stays in the PEL.
    async with acquire_tenant(tenant_slug) as conn:
        claimed = await ProcessedEventsRepository(conn).claim(event_id)

    if not claimed:
        # Already processed in a prior delivery — intentional dedup skip.
        logger.debug(
            "workflow event %s already processed by notification-service (dedup skip)", event_id
        )
        return

    # Interoperabilidad (E11): best-effort webhook delivery; dispatch swallows all errors.
    await dispatch_webhooks(envelope)

    event_type = envelope.get("event_type")
    payload_data = envelope.get("payload") or {}
    async with acquire_tenant(tenant_slug) as conn:
        buzon_destino = await resolver_buzon_dependencia(conn, payload_data.get("to_dept"))
    for send in build_workflow_notifications(envelope, buzon_destino=buzon_destino):
        await send_notification(send, event_type=event_type)


async def process_document_event(data: dict[str, str]) -> None:
    """Consumidor del stream de document (orpycamcp.document.events).

    Reacciona a ``document.radicado.created`` notificando a la dependencia destino.
    Para cualquier otro event_type, aún despacha webhooks (E11) y consume el dedup
    slot para garantizar exactly-once webhook delivery.
    """
    envelope = events.parse_fields(data)
    event_id: str | None = envelope.get("event_id")
    tenant_slug: str | None = envelope.get("tenant_slug")

    # Events without event_id or tenant_slug cannot be deduplicated safely.
    if not event_id or not tenant_slug:
        raise ValueError(
            "document event missing event_id or tenant_slug "
            f"(event_id={event_id!r}, tenant_slug={tenant_slug!r})"
        )

    # Claim the event_id before any external effect.
    async with acquire_tenant(tenant_slug) as conn:
        claimed = await ProcessedEventsRepository(conn).claim(event_id)

    if not claimed:
        logger.debug(
            "document event %s already processed by notification-service (dedup skip)", event_id
        )
        return

    # Interoperabilidad (E11): best-effort webhook delivery for all event types.
    await dispatch_webhooks(envelope)

    event_type = envelope.get("event_type")
    for send in build_document_notifications(envelope):
        await send_notification(send, event_type=event_type)
