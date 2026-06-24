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

import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.core.database import get_tenant_conn
from app.schemas.anulacion import (
    AnulacionDecision,
    AnulacionResponse,
    AnulacionSolicitud,
)
from app.services.anulacion_service import AnulacionService

router = APIRouter(prefix="/api/v1/documents", tags=["anulacion"])


def _actor(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


@router.post(
    "/{document_id}/anulacion",
    response_model=AnulacionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar anulación de un radicado (paso 1)",
)
async def solicitar_anulacion(
    document_id: uuid.UUID,
    payload: AnulacionSolicitud,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AnulacionResponse:
    return await AnulacionService(conn).solicitar(document_id, payload, _actor(request))


@router.get("/{document_id}/anulacion", response_model=AnulacionResponse)
async def get_anulacion(
    document_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AnulacionResponse:
    return await AnulacionService(conn).get(document_id)


@router.post(
    "/{document_id}/anulacion/aprobar",
    response_model=AnulacionResponse,
    summary="Aprobar la anulación (paso 2; supervisor) → radicado anulado",
)
async def aprobar_anulacion(
    document_id: uuid.UUID,
    payload: AnulacionDecision,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AnulacionResponse:
    return await AnulacionService(conn).aprobar(document_id, payload, _actor(request))


@router.post(
    "/{document_id}/anulacion/rechazar",
    response_model=AnulacionResponse,
    summary="Rechazar la anulación (paso 2; supervisor)",
)
async def rechazar_anulacion(
    document_id: uuid.UUID,
    payload: AnulacionDecision,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AnulacionResponse:
    return await AnulacionService(conn).rechazar(document_id, payload, _actor(request))
