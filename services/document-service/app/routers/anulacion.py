# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Anulación de radicado en dos pasos (solicitar/aprobar/rechazar)."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_redis, get_tenant_conn
from app.schemas.anulacion import (
    AnulacionDecision,
    AnulacionResponse,
    AnulacionSolicitud,
)
from app.services.anulacion_service import AnulacionService

router = APIRouter(prefix="/api/v1/documents", tags=["anulacion"])

_TENANT_HEADER = "X-Tenant-Slug"


def _actor(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def _tenant_slug(request: Request) -> str:
    # get_tenant_conn ya garantiza que el header está presente y es válido.
    return request.headers.get(_TENANT_HEADER, "")


@router.post(
    "/{document_id}/anulacion",
    response_model=AnulacionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar anulación de un radicado (paso 1)",
    dependencies=[Depends(require_permission("PERM_SOL_ANULAR"))],
)
async def solicitar_anulacion(
    document_id: uuid.UUID,
    payload: AnulacionSolicitud,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AnulacionResponse:
    """Solicita la anulación (paso 1). No-read-up: 404 neutro si el radicado no
    existe o supera el clearance del llamante (RF-SEG-08)."""
    clearance = await resolve_caller_clearance(conn, _actor(request))
    return await AnulacionService(conn).solicitar(document_id, payload, _actor(request), clearance)


@router.get("/{document_id}/anulacion", response_model=AnulacionResponse)
async def get_anulacion(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AnulacionResponse:
    """Estado de la anulación del radicado.

    No-read-up (RF-SEG-08): la causal y el motivo describen el documento, así que
    un radicado sobre-clearance devuelve el mismo 404 que uno sin anulaciones.
    """
    clearance = await resolve_caller_clearance(conn, _actor(request))
    return await AnulacionService(conn).get(document_id, clearance)


@router.post(
    "/{document_id}/anulacion/aprobar",
    response_model=AnulacionResponse,
    summary="Aprobar la anulación (paso 2; supervisor) → radicado anulado",
    dependencies=[Depends(require_permission("PERM_PANU_CODI"))],
)
async def aprobar_anulacion(
    document_id: uuid.UUID,
    payload: AnulacionDecision,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    redis: Any = Depends(get_redis),
) -> AnulacionResponse:
    """Aprueba la anulación (paso 2). No-read-up: aprobar la anulación de un
    radicado que no se puede ver sería mutar su ciclo de vida sin autoridad
    (Ley 594/2000 art. 28)."""
    clearance = await resolve_caller_clearance(conn, _actor(request))
    return await AnulacionService(conn, redis).aprobar(
        document_id,
        payload,
        _actor(request),
        clearance,
        tenant_slug=_tenant_slug(request),
    )


@router.post(
    "/{document_id}/anulacion/rechazar",
    response_model=AnulacionResponse,
    summary="Rechazar la anulación (paso 2; supervisor)",
    dependencies=[Depends(require_permission("PERM_PANU_CODI"))],
)
async def rechazar_anulacion(
    document_id: uuid.UUID,
    payload: AnulacionDecision,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AnulacionResponse:
    """Rechaza la anulación (paso 2). Mismo gate de clearance que aprobar."""
    clearance = await resolve_caller_clearance(conn, _actor(request))
    return await AnulacionService(conn).rechazar(document_id, payload, _actor(request), clearance)
