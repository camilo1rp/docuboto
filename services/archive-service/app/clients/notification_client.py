# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente de notification-service para la alerta de disposición vencida (E04).

Gemelo del de signature-service (`app/core/http_clients.py::send_otp_email`), y
por el mismo camino: `POST /api/v1/notifications/send` está **sellado con
`X-Internal-Token`** (D-02) porque el gateway lo expone bajo
`/api/v1/notifications/` y sin ese sello cualquier JWT válido podría enviar
correo con la identidad SMTP de la institución. El gateway NUNCA reenvía esa
cabecera (no está en `FORWARDED_REQUEST_HEADERS`), así que solo la alcanza un
llamante interno con el secreto compartido.

**Best-effort a propósito**: un fallo de correo no puede tumbar la barrida de
vencimientos ni dejar a los demás tenants sin avisar. Se registra y se sigue —
pero se registra de verdad, porque «no se envió» y «no se pudo enviar» son
cosas distintas y la segunda alguien tiene que poder verla.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class NotificationDeliveryError(RuntimeError):
    """No se pudo entregar el aviso a notification-service."""


async def enviar_aviso(
    *, recipient_email: str, subject: str, body: str, tenant_slug: str
) -> None:
    """Envía un aviso por correo. Lanza `NotificationDeliveryError` si no se
    pudo entregar — el llamante decide si eso aborta algo o solo se registra."""
    if not settings.notification_internal_token:
        raise NotificationDeliveryError(
            "NOTIFICATION_INTERNAL_TOKEN no configurado: el aviso no se envía. "
            "Sin el sello, notification-service rechaza el envío -- y mandar el "
            "correo sin él sería peor."
        )
    payload = {
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "tenant_slug": tenant_slug,
        # El cuerpo no lleva ni asuntos ni números de radicado (ver
        # `disposicion_alerta.py`): es persistible sin reservas.
        "sensible": False,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.notification_service_url, timeout=5.0
        ) as client:
            resp = await client.post(
                "/api/v1/notifications/send",
                json=payload,
                headers={"X-Internal-Token": settings.notification_internal_token},
            )
    except httpx.RequestError as exc:
        raise NotificationDeliveryError(f"notification-service inalcanzable: {exc}") from exc

    if resp.status_code not in (200, 201, 202):
        raise NotificationDeliveryError(
            f"notification-service → HTTP {resp.status_code}: {resp.text[:200]}"
        )
