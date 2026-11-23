# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Envío de correo que piden otros dominios (OTP de firma, avisos de archive).

Era `POST /api/v1/notifications/send`, sellado con token interno para que
ningún JWT pudiera mandar correo con la identidad SMTP de la institución. En
un solo proceso no hay ruta que sellar (ADR-047 §1): solo se llega por aquí.
"""

from __future__ import annotations

from notification.schemas.notification import NotificationSend
from notification.services import notification_service


class NotificationDeliveryError(RuntimeError):
    """El correo no salió. El llamante decide si eso aborta algo o solo se registra."""


async def send(
    *,
    recipient_email: str,
    subject: str,
    body: str,
    tenant_slug: str,
    sensible: bool = False,
) -> str:
    """Envía y **dice si se envió**: devuelve el id de la notificación o levanta.

    `send_notification` agota sus reintentos SMTP antes de volver. Para un OTP
    la entrega es la función: un envío fallido es un error, no un aviso para
    el historial (que lo registra igual). `sensible=True` envía el cuerpo real
    y persiste un marcador redactado (RF-FIR-13).
    """
    payload = NotificationSend(
        recipient_email=recipient_email,
        subject=subject,
        body=body,
        tenant_slug=tenant_slug,
        sensible=sensible,
    )
    try:
        notification = await notification_service.send_notification(payload)
    except Exception as exc:
        raise NotificationDeliveryError(f"no se pudo enviar el correo: {exc}") from exc
    if notification.status != "sent":
        raise NotificationDeliveryError(
            f"No se pudo entregar el correo tras {notification.attempts} intentos."
        )
    return notification.id
