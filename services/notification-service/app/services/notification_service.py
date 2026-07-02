# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import asyncpg

from app.core.config import settings
from app.core.database import acquire_tenant
from app.core.email_client import send_email
from app.models.notification import Notification
from app.repositories.notification_repository import NotificationRepository
from app.schemas.notification import NotificationResponse, NotificationSend

logger = logging.getLogger(__name__)

# RF-FIR-13 (cierre de fuga de OTP, seguimiento de seguridad de E06 Inc.2):
# placeholder que reemplaza `body` en la copia PERSISTIDA/hasheada de un
# envío marcado `sensible=True` (p. ej. el correo con el código OTP del 2FA
# por correo). El envío SMTP real (`_send_with_retries`) SIEMPRE usa
# `payload.body` sin tocar — la redacción aplica solo aquí, nunca antes de
# `send_email()`.
REDACTED_BODY = "[contenido sensible omitido]"
# Motivo genérico que reemplaza `last_error` en la copia persistida de un envío
# sensible fallido (ver `send_notification`). El detalle real solo se registra en
# el log de warning, nunca en la fila del historial.
REDACTED_ERROR = "error de envío (detalle omitido por sensibilidad)"


async def _send_with_retries(
    payload: NotificationSend,
) -> tuple[str, int, str | None]:
    """Intenta enviar el email con reintentos y backoff (RF E16).

    Devuelve (status, attempts, last_error). status='sent' si algún intento tuvo
    éxito; 'failed' si se agotaron los SMTP_MAX_ATTEMPTS.
    """
    last_error: str | None = None
    max_attempts = max(1, settings.smtp_max_attempts)
    for attempt in range(1, max_attempts + 1):
        try:
            await send_email(
                host=settings.smtp_host,
                port=settings.smtp_port,
                from_addr=settings.smtp_from,
                to_addr=payload.recipient_email,
                subject=payload.subject,
                body=payload.body,
            )
            return "sent", attempt, None
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning(
                "Envío SMTP fallido (intento %d/%d) a %s: %s",
                attempt,
                max_attempts,
                payload.recipient_email,
                last_error,
            )
            if attempt < max_attempts:
                await asyncio.sleep(settings.smtp_retry_backoff_seconds * attempt)
    return "failed", max_attempts, last_error


async def send_notification(
    payload: NotificationSend,
    event_type: str | None = None,
) -> Notification:
    notification_id = str(uuid.uuid4())
    # El envío SMTP siempre usa `payload.body` real, sensible o no — la
    # redacción de abajo solo afecta a lo que se PERSISTE.
    status, attempts, last_error = await _send_with_retries(payload)

    persisted_body = REDACTED_BODY if payload.sensible else payload.body
    # Defensa en profundidad (hallazgo de auditoría Baja): en un envío sensible
    # que falla, no persistir el `str(exc)` crudo. Las excepciones de aiosmtplib
    # transportan códigos/respuesta del servidor SMTP (no el cuerpo), pero para
    # que el invariante "nada sensible se persiste" sea hermético, la fila guarda
    # un motivo genérico; el detalle real queda solo en el `logger.warning` de
    # `_send_with_retries` (que tampoco incluye el body).
    persisted_error = (
        REDACTED_ERROR if (payload.sensible and last_error is not None) else last_error
    )
    notification = Notification(
        id=notification_id,
        recipient_email=payload.recipient_email,
        subject=payload.subject,
        body=persisted_body,
        status=status,
        sent_at=datetime.now(UTC).isoformat(),
        event_type=event_type,
        tenant_slug=payload.tenant_slug,
        attempts=attempts,
        last_error=persisted_error,
        sensible=payload.sensible,
    )

    await _persist(notification)
    return notification


async def _persist(notification: Notification) -> None:
    """Persiste el historial en el schema del tenant. Best-effort: un fallo de BD
    no debe tumbar el worker de eventos (se registra y se continúa)."""
    if not notification.tenant_slug:
        logger.warning("Notificación %s sin tenant_slug; no se persiste", notification.id)
        return
    try:
        async with acquire_tenant(notification.tenant_slug) as conn:
            await NotificationRepository(conn).insert(notification)
    except Exception:
        logger.warning(
            "No se pudo persistir la notificación %s (tenant %s)",
            notification.id,
            notification.tenant_slug,
            exc_info=True,
        )


async def get_notification_by_id(
    conn: asyncpg.Connection, notification_id: uuid.UUID, tenant_slug: str
) -> NotificationResponse | None:
    row = await NotificationRepository(conn).get_by_id(notification_id)
    return _row_to_response(row, tenant_slug) if row is not None else None


async def list_recent(
    conn: asyncpg.Connection, *, limit: int, tenant_slug: str
) -> list[NotificationResponse]:
    rows = await NotificationRepository(conn).list_recent(limit)
    return [_row_to_response(r, tenant_slug) for r in rows]


def _row_to_response(row: asyncpg.Record, tenant_slug: str) -> NotificationResponse:
    return NotificationResponse(
        id=str(row["id"]),
        recipient_email=row["recipient_email"],
        subject=row["subject"],
        status=row["status"],
        event_type=row["event_type"],
        tenant_slug=tenant_slug,
        sent_at=row["sent_at"].isoformat(),
        attempts=row["attempts"],
    )
