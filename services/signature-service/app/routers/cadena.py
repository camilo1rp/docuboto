# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cadena de firma secuencial — router (E06, ADR-016, RF-FIR-04/11/12/14).

Prefix: /api/v1/signature  (same prefix as signature.py — no route conflicts).
All routes require PERM_FIRMA (enforced in get_current_user, RF-FIR-12).
Firmante identity comes exclusively from JWT claims (RF-FIR-11).
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, Query, Response, status

from app.core.database import get_tenant_conn
from app.core.security import get_current_user
from app.schemas.cadena import (
    FirmarRequest,
    LoteRequest,
    LoteResponse,
    PendientesResponse,
    RechazoRequest,
    SolicitudCreate,
    SolicitudResponse,
)
from app.services.cadena_service import CadenaFirmaService

router = APIRouter(prefix="/api/v1/signature", tags=["firma-cadena"])


def _service(
    conn: asyncpg.Connection, claims: dict[str, Any]
) -> CadenaFirmaService:
    tenant_slug: str = claims.get("tenant_slug") or ""
    return CadenaFirmaService(conn, tenant_slug)


# ---------------------------------------------------------------------------
# Create chain
# ---------------------------------------------------------------------------


@router.post(
    "/requests",
    response_model=SolicitudResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a signature chain for a document object",
)
async def create_request(
    payload: SolicitudCreate,
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    actor = uuid.UUID(claims["user_id"])
    return await _service(conn, claims).crear(payload, actor)


# ---------------------------------------------------------------------------
# Read — by ID  (must be declared before GET /requests to avoid path collision)
# ---------------------------------------------------------------------------


@router.get(
    "/requests/{solicitud_id}",
    response_model=SolicitudResponse,
    summary="Get a signature chain by ID",
)
async def get_request(
    solicitud_id: uuid.UUID,
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    return await _service(conn, claims).get(solicitud_id)


# ---------------------------------------------------------------------------
# Read — by object (query params)
# ---------------------------------------------------------------------------


@router.get(
    "/requests",
    response_model=SolicitudResponse,
    summary="Get the open signature chain for a document object",
)
async def get_request_by_object(
    objeto_tipo: str = Query(..., min_length=1, max_length=40),
    objeto_id: str = Query(..., min_length=1),
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    return await _service(conn, claims).get_por_objeto(objeto_tipo, objeto_id)


# ---------------------------------------------------------------------------
# Inbox — pending turns for the current user
# ---------------------------------------------------------------------------


@router.get(
    "/pending",
    response_model=PendientesResponse,
    summary="List signature requests awaiting the current user's turn",
)
async def get_pending(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    response: Response = None,  # type: ignore[assignment]
) -> PendientesResponse:
    actor = uuid.UUID(claims["user_id"])
    items, total = await _service(conn, claims).pendientes(actor, page=page, size=size)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return PendientesResponse(items=items, total=total, page=page, size=size)


# ---------------------------------------------------------------------------
# Sign active turn
# ---------------------------------------------------------------------------


@router.post(
    "/requests/{solicitud_id}/sign",
    response_model=SolicitudResponse,
    summary="Sign the active turn in a signature chain",
)
async def sign_turn(
    solicitud_id: uuid.UUID,
    body: FirmarRequest | None = Body(default=None),
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    actor = uuid.UUID(claims["user_id"])
    return await _service(conn, claims).firmar(solicitud_id, actor, body)


# ---------------------------------------------------------------------------
# Reject active turn
# ---------------------------------------------------------------------------


@router.post(
    "/requests/{solicitud_id}/reject",
    response_model=SolicitudResponse,
    summary="Reject the active turn — stops the entire chain",
)
async def reject_turn(
    solicitud_id: uuid.UUID,
    body: RechazoRequest,
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    actor = uuid.UUID(claims["user_id"])
    return await _service(conn, claims).rechazar(solicitud_id, actor, body.motivo)


# ---------------------------------------------------------------------------
# Batch sign
# ---------------------------------------------------------------------------


@router.post(
    "/batch",
    response_model=LoteResponse,
    summary="Batch-sign multiple active turns for the current user",
)
async def batch_sign(
    body: LoteRequest,
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> LoteResponse:
    actor = uuid.UUID(claims["user_id"])
    return await _service(conn, claims).firmar_lote(body.solicitud_ids, actor)
