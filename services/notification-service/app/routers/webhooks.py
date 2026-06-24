# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""CRUD de suscripciones de webhooks (E11, ADR-018)."""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.database import get_tenant_conn
from app.repositories.webhook_repository import WebhookRepository
from app.schemas.webhook import WebhookCreate, WebhookResponse

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> WebhookResponse:
    row = await WebhookRepository(conn).insert(
        url=str(payload.url),
        event_types=payload.event_types,
        secret=payload.secret,
    )
    return WebhookResponse(**dict(row))


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    response: Response,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[WebhookResponse]:
    rows = await WebhookRepository(conn).list()
    response.headers["X-Total-Count"] = str(len(rows))
    return [WebhookResponse(**dict(r)) for r in rows]


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    if not await WebhookRepository(conn).delete(webhook_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "webhook_not_found", "detail": str(webhook_id), "status": 404},
        )
