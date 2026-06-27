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
from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_tenant_conn
from app.schemas.postal import ShipmentCreate, ShipmentResponse, ShipmentStatusUpdate
from app.services.postal_service import PostalService

router = APIRouter(tags=["postal"])

_USER_HEADER = "X-User-Id"


def _caller_id(request: Request) -> uuid.UUID | None:
    """Extract caller UUID from X-User-Id header; returns None on missing/invalid value."""
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


@router.post(
    "/api/v1/documents/{document_id}/envios",
    response_model=ShipmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Despachar un radicado por operador postal (E20)",
    dependencies=[Depends(require_permission("PERM_RADI_SALIDA"))],
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
    dependencies=[Depends(require_permission("PERM_RADI_SALIDA"))],
)
async def list_envios(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[ShipmentResponse]:
    # RF-SEG-08: filter out shipments whose parent radicado exceeds caller's clearance.
    clearance = await resolve_caller_clearance(conn, _caller_id(request))
    return await PostalService(conn).list_for_radicado(document_id, clearance)


@router.get(
    "/api/v1/envios",
    response_model=list[ShipmentResponse],
    summary="Listado transversal de envíos del tenant (bandeja, E20)",
    dependencies=[Depends(require_permission("PERM_RADI_SALIDA"))],
)
async def list_envios_global(
    request: Request,
    response: Response,
    estado: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[ShipmentResponse]:
    # RF-SEG-08: apply no-read-up filter; fail-closed to PUBLICA if identity absent.
    clearance = await resolve_caller_clearance(conn, _caller_id(request))
    shipments, total = await PostalService(conn).list_all(
        estado=estado, page=page, size=size, clearance=clearance
    )
    response.headers["X-Total-Count"] = str(total)
    return shipments


@router.get(
    "/api/v1/envios/{shipment_id}",
    response_model=ShipmentResponse,
    dependencies=[Depends(require_permission("PERM_RADI_SALIDA"))],
)
async def get_envio(
    shipment_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ShipmentResponse:
    # RF-SEG-08: return 404 if the parent radicado is above caller's clearance.
    clearance = await resolve_caller_clearance(conn, _caller_id(request))
    return await PostalService(conn).get(shipment_id, clearance)


@router.post(
    "/api/v1/envios/{shipment_id}/estado",
    response_model=ShipmentResponse,
    summary="Actualizar estado de entrega (callback/sondeo del operador) (E20)",
    dependencies=[Depends(require_permission("PERM_RADI_SALIDA"))],
)
async def update_estado(
    shipment_id: uuid.UUID,
    payload: ShipmentStatusUpdate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ShipmentResponse:
    return await PostalService(conn).update_estado(shipment_id, payload)
