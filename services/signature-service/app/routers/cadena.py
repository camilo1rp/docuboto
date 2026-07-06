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
All routes require PERM_FIRMA (enforced by require_permission at router level, RF-FIR-12).
Firmante identity comes exclusively from X-User-Id header (RF-FIR-11) — the internal
auth_users.id UUID resolved by auth-service /validate with lazy provisioning.

2FA (RF-FIR-13), two alternative factors gated in CadenaFirmaService — only
for the PERSONAL signature of the chain (the institutional seal of the
index, /sign-xml in signature.py, is a different router and never calls
CadenaFirmaService.firmar, so it is out of scope for this gate by
construction):
  - OTP-correo (E06 Inc.2): POST /requests/{id}/challenge emits a one-time
    OTP delivered by email; POST /requests/{id}/sign and POST /batch then
    accept challenge_id+otp.
  - TOTP (RFC 6238, E06): POST /requests/{id}/sign and POST /batch accept
    totp_code instead — verified against auth-service (no local challenge
    row). When both are present, TOTP takes priority (see
    CadenaFirmaService._gate_2fa).
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Body, Depends, Query, Request, Response, status

from app.core.clearance import get_actor, require_permission
from app.core.database import get_tenant_conn
from app.schemas.cadena import (
    ChallengeResponse,
    FirmarRequest,
    LoteRequest,
    LoteResponse,
    PendientesResponse,
    RechazoRequest,
    SolicitudCreate,
    SolicitudResponse,
)
from app.services.cadena_service import CadenaFirmaService

router = APIRouter(
    prefix="/api/v1/signature",
    tags=["firma-cadena"],
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)


def _service(conn: asyncpg.Connection, tenant_slug: str) -> CadenaFirmaService:
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
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    return await _service(conn, tenant_slug).crear(payload, actor)


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
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    return await CadenaFirmaService(conn, "").get(solicitud_id, actor)


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
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    return await CadenaFirmaService(conn, "").get_por_objeto(objeto_tipo, objeto_id, actor)


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
    request: Request = None,  # type: ignore[assignment]
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    response: Response = None,  # type: ignore[assignment]
) -> PendientesResponse:
    tenant_slug = request.headers.get("X-Tenant-Slug", "") if request else ""
    items, total = await _service(conn, tenant_slug).pendientes(actor, page=page, size=size)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return PendientesResponse(items=items, total=total, page=page, size=size)


# ---------------------------------------------------------------------------
# Emit 2FA challenge (E06 Inc.2, RF-FIR-13)
# ---------------------------------------------------------------------------


@router.post(
    "/requests/{solicitud_id}/challenge",
    response_model=ChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Emit a one-time OTP-by-email 2FA challenge for the caller's active turn",
)
async def create_challenge(
    solicitud_id: uuid.UUID,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ChallengeResponse:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    authorization = request.headers.get("Authorization")
    return await _service(conn, tenant_slug).emitir_challenge(solicitud_id, actor, authorization)


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
    request: Request,
    body: FirmarRequest | None = Body(default=None),
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    authorization = request.headers.get("Authorization")
    return await _service(conn, tenant_slug).firmar(solicitud_id, actor, body, authorization)


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
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SolicitudResponse:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    return await _service(conn, tenant_slug).rechazar(solicitud_id, actor, body.motivo)


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
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> LoteResponse:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    authorization = request.headers.get("Authorization")
    return await _service(conn, tenant_slug).firmar_lote(body.items, actor, authorization)
