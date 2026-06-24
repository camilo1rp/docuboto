# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Envíos postales de radicados (E20)."""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, status

from app.core.database import get_tenant_conn
from app.schemas.postal import ShipmentCreate, ShipmentResponse, ShipmentStatusUpdate
from app.services.postal_service import PostalService

router = APIRouter(tags=["postal"])


@router.post(
    "/api/v1/documents/{document_id}/envios",
    response_model=ShipmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Despachar un radicado por operador postal (E20)",
)
async def create_envio(
    document_id: uuid.UUID,
    payload: ShipmentCreate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ShipmentResponse:
    return await PostalService(conn).create(document_id, payload)


@router.get(
    "/api/v1/documents/{document_id}/envios",
    response_model=list[ShipmentResponse],
)
async def list_envios(
    document_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[ShipmentResponse]:
    return await PostalService(conn).list_for_radicado(document_id)


@router.get("/api/v1/envios/{shipment_id}", response_model=ShipmentResponse)
async def get_envio(
    shipment_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ShipmentResponse:
    return await PostalService(conn).get(shipment_id)


@router.post(
    "/api/v1/envios/{shipment_id}/estado",
    response_model=ShipmentResponse,
    summary="Actualizar estado de entrega (callback/sondeo del operador) (E20)",
)
async def update_estado(
    shipment_id: uuid.UUID,
    payload: ShipmentStatusUpdate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ShipmentResponse:
    return await PostalService(conn).update_estado(shipment_id, payload)
