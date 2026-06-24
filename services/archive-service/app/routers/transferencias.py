# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Transferencias documentales primarias/secundarias (E12)."""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.core.database import get_db_conn
from app.schemas.fisico import FuidResponse
from app.schemas.transferencia import (
    TransferenciaCreate,
    TransferenciaDecision,
    TransferenciaResponse,
)
from app.services.transferencia_service import TransferenciaService

router = APIRouter(prefix="/api/v1/transferencias", tags=["transferencias"])


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


def _actor(request: Request) -> str | None:
    return request.headers.get("X-Username") or request.headers.get("X-User-Id")


@router.post("", response_model=TransferenciaResponse, status_code=status.HTTP_201_CREATED)
async def create_transferencia(
    payload: TransferenciaCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).create(payload, _actor(request))


@router.get("", response_model=list[TransferenciaResponse])
async def list_transferencias(
    estado: str | None = Query(default=None),
    tipo: str | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TransferenciaResponse]:
    return await TransferenciaService(conn, tenant_slug).list(estado=estado, tipo=tipo)


@router.get("/{transferencia_id}", response_model=TransferenciaResponse)
async def get_transferencia(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).get(transferencia_id)


@router.post("/{transferencia_id}/enviar", response_model=TransferenciaResponse)
async def enviar(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).enviar(transferencia_id)


@router.post("/{transferencia_id}/recibir", response_model=TransferenciaResponse)
async def recibir(
    transferencia_id: uuid.UUID,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).recibir(transferencia_id, _actor(request))


@router.post("/{transferencia_id}/rechazar", response_model=TransferenciaResponse)
async def rechazar(
    transferencia_id: uuid.UUID,
    payload: TransferenciaDecision,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).rechazar(
        transferencia_id, payload, _actor(request)
    )


@router.get(
    "/{transferencia_id}/fuid",
    response_model=FuidResponse,
    summary="FUID entregable of the transfer (consumes E17, RF-TRA-02)",
)
async def transferencia_fuid(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FuidResponse:
    return await TransferenciaService(conn, tenant_slug).fuid(transferencia_id)
