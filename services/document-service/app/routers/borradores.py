# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Plantillas y borradores de Salida (paridad legado)."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.borrador import (
    BorradorCreate,
    BorradorResponse,
    BorradorUpdate,
    PlantillaCreate,
    PlantillaResponse,
)
from app.schemas.document import DocumentResponse
from app.services.borrador_service import BorradorService
from app.services.document_service import DocumentService, InvalidTenantError

router = APIRouter(tags=["borradores"])

_TENANT_HEADER = "X-Tenant-Slug"


def _slug(request: Request) -> str:
    return request.headers.get(_TENANT_HEADER, "")


def _user(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


# -- plantillas --------------------------------------------------------


@router.post("/api/v1/plantillas", response_model=PlantillaResponse, status_code=201)
async def crear_plantilla(
    payload: PlantillaCreate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> PlantillaResponse:
    return await BorradorService(conn).crear_plantilla(payload)


@router.get("/api/v1/plantillas", response_model=list[PlantillaResponse])
async def listar_plantillas(
    activo: bool | None = Query(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[PlantillaResponse]:
    return await BorradorService(conn).listar_plantillas(activo)


@router.delete("/api/v1/plantillas/{plantilla_id}", status_code=204)
async def borrar_plantilla(
    plantilla_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    await BorradorService(conn).borrar_plantilla(plantilla_id)


# -- borradores --------------------------------------------------------


@router.post("/api/v1/borradores", response_model=BorradorResponse, status_code=201)
async def crear_borrador(
    payload: BorradorCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BorradorResponse:
    return await BorradorService(conn).crear(payload, _user(request))


@router.get("/api/v1/borradores", response_model=list[BorradorResponse])
async def listar_borradores(
    estado: str | None = Query(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[BorradorResponse]:
    return await BorradorService(conn).listar(estado)


@router.get("/api/v1/borradores/{borrador_id}", response_model=BorradorResponse)
async def get_borrador(
    borrador_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BorradorResponse:
    return await BorradorService(conn).get(borrador_id)


@router.patch("/api/v1/borradores/{borrador_id}", response_model=BorradorResponse)
async def editar_borrador(
    borrador_id: uuid.UUID,
    payload: BorradorUpdate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BorradorResponse:
    return await BorradorService(conn).editar(borrador_id, payload)


@router.post("/api/v1/borradores/{borrador_id}/aprobar", response_model=BorradorResponse)
async def aprobar_borrador(
    borrador_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BorradorResponse:
    return await BorradorService(conn).aprobar(borrador_id)


@router.post(
    "/api/v1/borradores/{borrador_id}/radicar",
    response_model=DocumentResponse,
    status_code=201,
    summary="Radicar el borrador → genera el radicado real",
)
async def radicar_borrador(
    borrador_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> DocumentResponse:
    try:
        doc_service = DocumentService(conn, _slug(request), tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )
    return await BorradorService(conn).radicar(borrador_id, doc_service, _user(request))
