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
  5. Only after a successful claim: dispatch webhooks (E11) + send notification.

Trade-off: if the worker dies between step 3 (claim) and step 5 (send), that
email is permanently lost.  Accepted: email is not the legal record; audit_log
and the radicado in document-service are the authoritative sources.

External effects after the claim (webhooks, email) are best-effort: any failure
is already handled by their own retry mechanisms.  They must NOT raise exceptions
that would propagate to stream_worker and trigger a no-ack for an event that has
already been claimed — that would cause infinite PEL churn (the dedup would block
reprocessing anyway).
"""

from __future__ import annotations

import logging

from orpycamcp_common import events

from app.core.database import acquire_tenant
from app.repositories.processed_events_repository import ProcessedEventsRepository
from app.schemas.notification import NotificationSend
from app.services.notification_service import send_notification
from app.services.webhook_service import dispatch as dispatch_webhooks

logger = logging.getLogger(__name__)


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

    event_type = envelope.get("event_type")
    payload_data = envelope.get("payload") or {}

    # Interoperabilidad (E11): best-effort webhook delivery; dispatch swallows all errors.
    await dispatch_webhooks(envelope)

    if event_type == "workflow.step.created" and payload_data.get("action") == "assign":
        tracking_number = payload_data.get("tracking_number", "N/A")
        to_dept = payload_data.get("to_dept", "unknown")

        await send_notification(
            NotificationSend(
                recipient_email=f"{to_dept}@orpycamcp.local",
                subject=f"[OrpycaMCP] Nuevo radicado asignado: {tracking_number}",
                body=(
                    f"Se ha asignado un nuevo radicado a su dependencia.\n\n"
                    f"Número de radicado: {tracking_number}\n"
                    f"Dependencia destino: {to_dept}\n"
                    f"Inquilino: {tenant_slug}\n"
                ),
                tenant_slug=tenant_slug,
            ),
            event_type=event_type,
        )

    elif event_type == "workflow.step.overdue":
        tracking_number = payload_data.get("tracking_number", "N/A")
        to_dept = payload_data.get("to_dept", "unknown")
        due_at = payload_data.get("due_at")

        await send_notification(
            NotificationSend(
                recipient_email=f"{to_dept}@orpycamcp.local",
                subject=f"[OrpycaMCP] Trámite VENCIDO: {tracking_number}",
                body=(
                    f"El trámite del radicado {tracking_number} está vencido.\n\n"
                    f"Dependencia responsable: {to_dept}\n"
                    f"Fecha límite: {due_at or 'N/A'}\n"
                    f"Inquilino: {tenant_slug}\n"
                ),
                tenant_slug=tenant_slug,
            ),
            event_type=event_type,
        )


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

    if envelope.get("event_type") != "document.radicado.created":
        return

    payload_data = envelope.get("payload") or {}
    tracking_number = payload_data.get("tracking_number", "N/A")
    dest_dept_code = payload_data.get("dest_dept_code")

    recipient = (
        f"dependencia-{dest_dept_code}@orpycamcp.local"
        if dest_dept_code is not None
        else "ventanilla@orpycamcp.local"
    )

    await send_notification(
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
        ),
        event_type=envelope.get("event_type"),
    )
