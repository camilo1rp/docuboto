# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

from orpycamcp_common import events

from app.schemas.notification import NotificationSend
from app.services.notification_service import send_notification
from app.services.webhook_service import dispatch as dispatch_webhooks


async def process_event(data: dict[str, str]) -> None:
    """Consumidor del stream de workflow (sobre canónico de orpycamcp_common)."""
    envelope = events.parse_fields(data)
    event_type = envelope.get("event_type")
    payload_data = envelope.get("payload") or {}

    # Interoperabilidad (E11): entrega del evento a webhooks externos (best-effort).
    await dispatch_webhooks(envelope)

    if event_type == "workflow.step.created" and payload_data.get("action") == "assign":
        tracking_number = payload_data.get("tracking_number", "N/A")
        to_dept = payload_data.get("to_dept", "unknown")
        tenant_slug = envelope.get("tenant_slug")

        recipient = f"{to_dept}@orpycamcp.local"
        subject = f"[OrpycaMCP] Nuevo radicado asignado: {tracking_number}"
        body = (
            f"Se ha asignado un nuevo radicado a su dependencia.\n\n"
            f"Número de radicado: {tracking_number}\n"
            f"Dependencia destino: {to_dept}\n"
            f"Inquilino: {tenant_slug or 'N/A'}\n"
        )

        payload = NotificationSend(
            recipient_email=recipient,
            subject=subject,
            body=body,
            tenant_slug=tenant_slug,
        )
        await send_notification(payload, event_type=event_type)

    elif event_type == "workflow.step.overdue":
        # Alerta de vencimiento (RF-FLU-07): avisa a la dependencia responsable.
        tracking_number = payload_data.get("tracking_number", "N/A")
        to_dept = payload_data.get("to_dept", "unknown")
        due_at = payload_data.get("due_at")
        tenant_slug = envelope.get("tenant_slug")

        notification = NotificationSend(
            recipient_email=f"{to_dept}@orpycamcp.local",
            subject=f"[OrpycaMCP] Trámite VENCIDO: {tracking_number}",
            body=(
                f"El trámite del radicado {tracking_number} está vencido.\n\n"
                f"Dependencia responsable: {to_dept}\n"
                f"Fecha límite: {due_at or 'N/A'}\n"
                f"Inquilino: {tenant_slug or 'N/A'}\n"
            ),
            tenant_slug=tenant_slug,
        )
        await send_notification(notification, event_type=event_type)


async def process_document_event(data: dict[str, str]) -> None:
    """Consumidor del stream de document (sobre canónico de orpycamcp_common).

    Reacciona a la radicación de un documento notificando a la dependencia
    destino. Los campos de dominio viajan dentro de ``payload`` del sobre.
    """
    envelope = events.parse_fields(data)
    # Interoperabilidad (E11): entrega del evento a webhooks externos (best-effort).
    await dispatch_webhooks(envelope)
    if envelope.get("event_type") != "document.radicado.created":
        return

    tenant_slug = envelope.get("tenant_slug")
    payload_data = envelope.get("payload") or {}
    tracking_number = payload_data.get("tracking_number", "N/A")
    dest_dept_code = payload_data.get("dest_dept_code")

    recipient = (
        f"dependencia-{dest_dept_code}@orpycamcp.local"
        if dest_dept_code is not None
        else "ventanilla@orpycamcp.local"
    )
    subject = f"[OrpycaMCP] Documento radicado: {tracking_number}"
    body = (
        f"Se ha radicado un nuevo documento.\n\n"
        f"Número de radicado: {tracking_number}\n"
        f"Dependencia destino: "
        f"{dest_dept_code if dest_dept_code is not None else 'sin asignar'}\n"
        f"Inquilino: {tenant_slug or 'N/A'}\n"
    )

    notification = NotificationSend(
        recipient_email=recipient,
        subject=subject,
        body=body,
        tenant_slug=tenant_slug,
    )
    await send_notification(notification, event_type=envelope.get("event_type"))
