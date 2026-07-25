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
from app.core.database import get_redis, get_tenant_conn, tenant_schema
from app.schemas.postal import (
    CallbackResult,
    ShipmentCreate,
    ShipmentResponse,
    ShipmentStatusUpdate,
    TrackingResponse,
)
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
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ShipmentResponse:
    # RF-SEG-08: no se despacha un radicado que el llamante no puede ver — era la
    # única ruta de este archivo sin el gate que sus cinco hermanas ya aplican.
    clearance = await resolve_caller_clearance(conn, _caller_id(request))
    return await PostalService(conn).create(document_id, payload, clearance)


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
    summary="Actualizar estado de entrega manualmente (despacho humano) (E20)",
    dependencies=[Depends(require_permission("PERM_RADI_SALIDA"))],
)
async def update_estado(
    shipment_id: uuid.UUID,
    payload: ShipmentStatusUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ShipmentResponse:
    # RF-SEG-08 (cierra D-14): la transición manual pasa el clearance del llamante
    # → un envío de radicado sobre-clearance colapsa a 404 (sin read-up en el write).
    clearance = await resolve_caller_clearance(conn, _caller_id(request))
    return await PostalService(conn).update_estado(shipment_id, payload, clearance)


@router.get(
    "/api/v1/envios/{shipment_id}/tracking",
    response_model=TrackingResponse,
    summary="Consultar estado por guía contra el operador (pull, F5)",
    dependencies=[Depends(require_permission("PERM_RADI_SALIDA"))],
)
async def tracking(
    shipment_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> TrackingResponse:
    clearance = await resolve_caller_clearance(conn, _caller_id(request))
    return await PostalService(conn).tracking(shipment_id, clearance)


@router.post(
    "/api/v1/public/postal/callback/{tenant_slug}",
    response_model=CallbackResult,
    summary="Webhook ENTRANTE del operador postal — estado de entrega (E20 F5)",
)
async def postal_callback(
    tenant_slug: str,
    request: Request,
    redis: object = Depends(get_redis),
) -> CallbackResult:
    """Callback del operador (4-72/Servientrega). Bajo `/api/v1/public/` → el
    gateway NO exige JWT; la autenticación es HMAC per-tenant/per-operador
    (identidad propia del operador, separada del permiso humano de despacho —
    cierra D-14 #3). El tenant va en el path (el operador no manda JWT); el
    secreto vive en el schema de ESE tenant, así que una firma válida prueba que
    el emisor lo conoce y no puede falsificar callbacks de otro tenant.
    """
    schema = tenant_schema(tenant_slug)  # valida el slug (400 si es inválido)
    raw = await request.body()
    signature = request.headers.get("X-OrpycaMCP-Signature")
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            svc = PostalService(conn, redis=redis, tenant_slug=tenant_slug)
            return await svc.apply_operator_callback(raw, signature, tenant_slug)
        finally:
            await conn.execute("SET search_path = public")
