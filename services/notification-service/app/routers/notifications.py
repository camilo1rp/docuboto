# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.clearance import require_permission
from app.core.database import get_tenant_conn
from app.core.internal_auth import require_internal_token
from app.schemas.notification import (
    NotificationAccepted,
    NotificationResponse,
    NotificationSend,
)
from app.services import notification_service

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "Falta la cabecera X-Tenant-Slug",
                "status": 400,
            },
        )
    return x_tenant_slug


@router.post(
    "/send",
    response_model=NotificationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    # Endpoint SERVICIO-A-SERVICIO (D-02): lo llama signature-service para los OTP
    # de 2FA. Sellado con token interno — el gateway lo expone bajo
    # `/api/v1/notifications/`, y sin este gate cualquier JWT válido podría enviar
    # correo arbitrario con la identidad SMTP de la institución (phishing/spam) y
    # suplantar el canal de OTP. Los demás envíos entran por el stream de eventos
    # (`event_consumer` → `send_notification()` en proceso), no por esta ruta.
    dependencies=[Depends(require_internal_token)],
)
async def send_notification(payload: NotificationSend) -> NotificationAccepted:
    """Envía y **dice si se envió**.

    El envío es síncrono: `send_notification` agota sus reintentos SMTP antes
    de volver, y el resultado queda en `notification.status`. Aun así esta ruta
    respondía `202 Accepted` pasara lo que pasara — el servicio sabía que había
    fallado, lo guardaba como `failed` con sus tres intentos, y contestaba que
    sí.

    Lo que eso rompía está un servicio más allá. El único llamante de esta ruta
    es el **OTP de segundo factor** de signature-service, que tiene escrito su
    fail-closed: `503 canal_2fa_no_disponible` si el correo no sale. Con el 202
    incondicional ese gate **no podía dispararse nunca por esta causa**: el
    firmante veía «Enviamos un código, vence en 4:48» y esperaba un correo que
    nadie había mandado. Un fail-closed inalcanzable es peor que no tenerlo,
    porque se lee como una garantía.

    Y para un OTP la entrega **es** la función: un código con cinco minutos de
    vida que se reintenta en segundo plano no sirve de nada. Por eso aquí un
    envío fallido es un error de la petición, no un aviso para el historial.

    La notificación se persiste igual —el historial tiene que registrar el
    intento fallido, que es justamente lo que permitió encontrar esto—; lo que
    cambia es la respuesta.
    """
    notification = await notification_service.send_notification(payload)
    if notification.status != "sent":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "envio_fallido",
                "detail": (
                    "No se pudo entregar el correo tras "
                    f"{notification.attempts} intentos."
                ),
                "status": 502,
            },
        )
    return NotificationAccepted(id=notification.id)


@router.get(
    "/history",
    response_model=list[NotificationResponse],
    # Vista tenant-wide de TODAS las notificaciones (sin filtro por usuario) → es
    # un registro administrativo/auditoría, no un buzón personal. Exige admin: sin
    # el gate, cualquier usuario del tenant leía asuntos/cuerpos de correos ajenos.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def get_history(
    limit: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[NotificationResponse]:
    return await notification_service.list_recent(conn, limit=limit, tenant_slug=tenant_slug)


@router.get(
    "/{notification_id}",
    response_model=NotificationResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def get_notification(
    notification_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> NotificationResponse:
    notification = await notification_service.get_notification_by_id(
        conn, notification_id, tenant_slug
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "notification_not_found",
                "detail": "No existe la notificación solicitada",
                "status": 404,
            },
        )
    return notification
