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

from app.core.database import get_tenant_conn
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
                "detail": "X-Tenant-Slug header is required",
                "status": 400,
            },
        )
    return x_tenant_slug


@router.post(
    "/send",
    response_model=NotificationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_notification(payload: NotificationSend) -> NotificationAccepted:
    notification = await notification_service.send_notification(payload)
    return NotificationAccepted(id=notification.id)


@router.get("/history", response_model=list[NotificationResponse])
async def get_history(
    limit: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[NotificationResponse]:
    return await notification_service.list_recent(conn, limit=limit, tenant_slug=tenant_slug)


@router.get("/{notification_id}", response_model=NotificationResponse)
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
                "detail": f"Notification {notification_id} not found",
                "status": 404,
            },
        )
    return notification
