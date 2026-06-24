# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Archivo físico: ubicaciones, unidades de conservación y localización (E17).
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from app.core.database import get_db_conn
from app.schemas.fisico import (
    ExpedienteEnUnidad,
    ExpedienteUnidadLink,
    FuidResponse,
    MovimientoResponse,
    PrestamoCreate,
    PrestamoResponse,
    UbicacionCreate,
    UbicacionResponse,
    UnidadCreate,
    UnidadDeExpediente,
    UnidadResponse,
)
from app.services.fisico_service import FisicoService

router = APIRouter(prefix="/api/v1", tags=["archivo-fisico"])


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


# -- ubicaciones -------------------------------------------------------


@router.post("/ubicaciones", response_model=UbicacionResponse, status_code=status.HTTP_201_CREATED)
async def create_ubicacion(
    payload: UbicacionCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UbicacionResponse:
    return await FisicoService(conn, tenant_slug).create_ubicacion(payload)


@router.get("/ubicaciones", response_model=list[UbicacionResponse])
async def list_ubicaciones(
    parent_id: uuid.UUID | None = Query(default=None),
    activo: bool | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[UbicacionResponse]:
    return await FisicoService(conn, tenant_slug).list_ubicaciones(
        parent_id=parent_id, activo=activo
    )


@router.get("/ubicaciones/{ubicacion_id}", response_model=UbicacionResponse)
async def get_ubicacion(
    ubicacion_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UbicacionResponse:
    return await FisicoService(conn, tenant_slug).get_ubicacion(ubicacion_id)


# -- unidades de conservación -----------------------------------------


@router.post("/unidades", response_model=UnidadResponse, status_code=status.HTTP_201_CREATED)
async def create_unidad(
    payload: UnidadCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UnidadResponse:
    return await FisicoService(conn, tenant_slug).create_unidad(payload)


@router.get("/unidades", response_model=list[UnidadResponse])
async def list_unidades(
    ubicacion_id: uuid.UUID | None = Query(default=None),
    tipo: str | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[UnidadResponse]:
    return await FisicoService(conn, tenant_slug).list_unidades(
        ubicacion_id=ubicacion_id, tipo=tipo
    )


@router.get("/unidades/{unidad_id}", response_model=UnidadResponse)
async def get_unidad(
    unidad_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UnidadResponse:
    return await FisicoService(conn, tenant_slug).get_unidad(unidad_id)


# -- vínculo N:M y localización ---------------------------------------


@router.post(
    "/unidades/{unidad_id}/expedientes",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Link an expediente to a conservation unit (folio range)",
)
async def link_expediente(
    unidad_id: uuid.UUID,
    payload: ExpedienteUnidadLink,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    await FisicoService(conn, tenant_slug).link_expediente(unidad_id, payload)


@router.get(
    "/unidades/{unidad_id}/expedientes",
    response_model=list[ExpedienteEnUnidad],
    summary="What does this unit contain",
)
async def expedientes_in_unit(
    unidad_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteEnUnidad]:
    return await FisicoService(conn, tenant_slug).expedientes_in_unit(unidad_id)


@router.get(
    "/expedientes/{expediente_id}/unidades",
    response_model=list[UnidadDeExpediente],
    summary="Where is this expediente (physical units + signatura)",
)
async def units_of_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[UnidadDeExpediente]:
    return await FisicoService(conn, tenant_slug).units_of_expediente(expediente_id)


# -- préstamos y movimientos (RF-ARF-07/08) ---------------------------


def _performed_by(request: Request) -> str | None:
    return request.headers.get("X-Username") or request.headers.get("X-User-Id")


@router.post(
    "/unidades/{unidad_id}/prestamos",
    response_model=PrestamoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prestamo(
    unidad_id: uuid.UUID,
    payload: PrestamoCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PrestamoResponse:
    return await FisicoService(conn, tenant_slug).create_prestamo(
        unidad_id, payload, _performed_by(request)
    )


@router.get("/unidades/{unidad_id}/prestamos", response_model=list[PrestamoResponse])
async def list_prestamos_of_unit(
    unidad_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[PrestamoResponse]:
    return await FisicoService(conn, tenant_slug).list_prestamos(estado=None, unidad_id=unidad_id)


@router.get("/prestamos", response_model=list[PrestamoResponse])
async def list_prestamos(
    estado: str | None = Query(default=None, description="prestado|devuelto|vencido"),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[PrestamoResponse]:
    return await FisicoService(conn, tenant_slug).list_prestamos(estado=estado, unidad_id=None)


@router.post("/prestamos/{prestamo_id}/devolver", response_model=PrestamoResponse)
async def devolver_prestamo(
    prestamo_id: uuid.UUID,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PrestamoResponse:
    return await FisicoService(conn, tenant_slug).devolver_prestamo(
        prestamo_id, _performed_by(request)
    )


@router.get(
    "/unidades/{unidad_id}/movimientos",
    response_model=list[MovimientoResponse],
    summary="Append-only physical movement history of a unit (RF-ARF-08)",
)
async def list_movimientos(
    unidad_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[MovimientoResponse]:
    return await FisicoService(conn, tenant_slug).list_movimientos(unidad_id)


# -- FUID (RF-ARF-10) -------------------------------------------------


@router.get(
    "/fuid",
    response_model=FuidResponse,
    summary="Formato Único de Inventario Documental (RF-ARF-10)",
)
async def get_fuid(
    ubicacion_id: uuid.UUID | None = Query(default=None),
    trd_serie_id: uuid.UUID | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FuidResponse:
    return await FisicoService(conn, tenant_slug).generate_fuid(
        ubicacion_id=ubicacion_id, trd_serie_id=trd_serie_id
    )


@router.get("/fuid.xml", summary="FUID export as XML (RF-ARF-10)")
async def get_fuid_xml(
    ubicacion_id: uuid.UUID | None = Query(default=None),
    trd_serie_id: uuid.UUID | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> Response:
    xml = await FisicoService(conn, tenant_slug).generate_fuid_xml(
        ubicacion_id=ubicacion_id, trd_serie_id=trd_serie_id
    )
    return Response(content=xml, media_type="application/xml")
