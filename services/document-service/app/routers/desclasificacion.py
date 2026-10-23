# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Desclasificación con segunda persona y catálogo de causales (C4/C12, ADR-039).

Dos routers:
- `router` (`/api/v1/documents/...`): catálogo de causales y solicitud/historial de un
  radicado. Se monta ANTES que `documents_router` para que el literal
  `/causales-reserva` no lo capture `/{document_id}`.
- `cola_router` (`/api/v1/desclasificaciones`): el trabajo del aprobador, recurso propio
  (no una subruta de un documento), como `/api/v1/anulaciones`.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.causales_reserva import CAUSALES, NIVEL_DOBLE_FIRMA, PLAZO_MAX_MESES
from app.core.clearance import require_permission
from app.core.database import get_redis, get_tenant_conn
from app.schemas.desclasificacion import (
    CausalesReservaResponse,
    CausalReserva,
    DesclasificacionAprobada,
    DesclasificacionPendiente,
    DesclasificacionRechazo,
    DesclasificacionRequest,
    DesclasificacionResponse,
    DesclasificacionRetiro,
)
from app.services.desclasificacion_service import DesclasificacionService

router = APIRouter(prefix="/api/v1/documents", tags=["desclasificacion"])
cola_router = APIRouter(prefix="/api/v1/desclasificaciones", tags=["desclasificacion"])

_TENANT_HEADER = "X-Tenant-Slug"


def _actor(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def _servicio(request: Request, conn: asyncpg.Connection, redis: Any = None) -> DesclasificacionService:
    # get_tenant_conn ya garantiza que el header está presente y es válido.
    return DesclasificacionService(conn, request.headers.get(_TENANT_HEADER, ""), redis)


@router.get(
    "/causales-reserva",
    response_model=CausalesReservaResponse,
    summary="Catálogo cerrado de causales de clasificación y reserva (Ley 1712/2014 arts. 18-19)",
)
async def causales_reserva(_: asyncpg.Connection = Depends(get_tenant_conn)) -> CausalesReservaResponse:
    """Sin permiso: es la ley, no un dato de la entidad. Lo usan la UI y el MCP para
    ofrecer solo causales válidas. Requiere tenant (el gateway autentica); no lee datos."""
    return CausalesReservaResponse(
        nivel_doble_firma=NIVEL_DOBLE_FIRMA,
        plazo_max_meses=PLAZO_MAX_MESES,
        causales=[CausalReserva.de(c) for c in CAUSALES.values()],
    )


@router.post(
    "/{document_id}/desclasificacion",
    response_model=DesclasificacionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar la desclasificación de un radicado clasificado (nivel 3) — paso 1",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=3))],
)
async def solicitar_desclasificacion(
    document_id: uuid.UUID,
    payload: DesclasificacionRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DesclasificacionResponse:
    """Bajar el nivel de un radicado CLASIFICADO lo decide otra persona (paso 2:
    `POST /api/v1/desclasificaciones/{id}/aprobar`). 404 neutro si no existe o supera
    el clearance; 409 `no_requiere_aprobacion` si el radicado está por debajo del
    umbral (use el PATCH directo); 409 `desclasificacion_pendiente` si ya hay una."""
    return await _servicio(request, conn).solicitar(document_id, payload, _actor(request))


@router.get(
    "/{document_id}/desclasificacion",
    response_model=list[DesclasificacionResponse],
    summary="Historial de solicitudes de desclasificación de un radicado",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR"))],
)
async def historial_desclasificacion(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DesclasificacionResponse]:
    """Vacío tanto si no hay solicitudes como si el radicado supera el clearance."""
    return await _servicio(request, conn).historial(document_id, _actor(request))


@cola_router.get(
    "",
    response_model=list[DesclasificacionPendiente],
    summary="Cola de solicitudes de desclasificación pendientes de decidir",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=4))],
)
async def cola_desclasificaciones(
    response: Response,
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[DesclasificacionPendiente]:
    """Pendientes vigentes, la más antigua primero. Filas y `X-Total-Count` salen de la
    MISMA condición (clearance incluido): el total no delata solicitudes sobre
    radicados que el llamante no alcanza. `propia` marca las del propio llamante."""
    items, total = await _servicio(request, conn).cola(_actor(request), page=page, size=size)
    response.headers["X-Total-Count"] = str(total)
    return items


@cola_router.post(
    "/{solicitud_id}/aprobar",
    response_model=DesclasificacionAprobada,
    summary="Aprobar (y ejecutar) la desclasificación — paso 2, otra persona",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=4))],
)
async def aprobar_desclasificacion(
    solicitud_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    redis: Any = Depends(get_redis),
) -> DesclasificacionAprobada:
    """200 = el nivel ya bajó (misma transacción: nivel, 2 asientos y evento
    `reclassified`). 404 neutro si no la alcanza; 409: `separacion_de_funciones`,
    `solicitud_caducada`, `solicitud_ya_resuelta`, `nivel_cambio`."""
    return await _servicio(request, conn, redis).aprobar(solicitud_id, _actor(request))


@cola_router.post(
    "/{solicitud_id}/rechazar",
    response_model=DesclasificacionResponse,
    summary="Rechazar la desclasificación (motivo obligatorio)",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=4))],
)
async def rechazar_desclasificacion(
    solicitud_id: uuid.UUID,
    payload: DesclasificacionRechazo,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DesclasificacionResponse:
    return await _servicio(request, conn).rechazar(
        solicitud_id, _actor(request), payload.resolucion_motivo
    )


@cola_router.post(
    "/{solicitud_id}/retirar",
    response_model=DesclasificacionResponse,
    summary="Retirar la propia solicitud de desclasificación",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=3))],
)
async def retirar_desclasificacion(
    solicitud_id: uuid.UUID,
    request: Request,
    payload: DesclasificacionRetiro | None = None,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DesclasificacionResponse:
    """Solo quien solicitó puede retirarla (409 `solo_el_solicitante`)."""
    return await _servicio(request, conn).retirar(
        solicitud_id, _actor(request), payload.motivo if payload else None
    )
