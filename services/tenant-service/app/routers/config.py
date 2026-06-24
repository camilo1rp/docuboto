# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de configuración por tenant (E14): festivos, días hábiles, parámetros.

NOTA (TODO): el gate de permiso `admin` del llamante se añade cuando el JWT esté
enriquecido (E08). Hoy el api-gateway media el acceso.
"""

import uuid
from datetime import date, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.calendar import add_business_days, colombian_holidays
from app.core.database import get_tenant_conn
from app.repositories.config_repository import ConfigRepository
from app.schemas.config import (
    BusinessDaysResult,
    HolidayCreate,
    HolidayResponse,
    HolidaySeedRequest,
    ParamResponse,
    ParamUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["config"])


def _actor(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


# --- festivos ---------------------------------------------------------------
@router.get("/config/holidays", response_model=list[HolidayResponse])
async def list_holidays(
    year: int | None = Query(None, ge=2000, le=2100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[HolidayResponse]:
    repo = ConfigRepository(conn)
    return [HolidayResponse(**dict(r)) for r in await repo.list_holidays(year)]


@router.post(
    "/config/holidays", response_model=HolidayResponse, status_code=status.HTTP_201_CREATED
)
async def create_holiday(
    payload: HolidayCreate, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> HolidayResponse:
    repo = ConfigRepository(conn)
    try:
        row = await repo.insert_holiday(
            fecha=payload.fecha,
            descripcion=payload.descripcion,
            tipo=payload.tipo,
            is_national=payload.is_national,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"{payload.fecha} already exists",
                "status": 409,
            },
        )
    return HolidayResponse(**dict(row))


@router.delete("/config/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    holiday_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> None:
    repo = ConfigRepository(conn)
    if not await repo.delete_holiday(holiday_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Holiday not found", "status": 404},
        )


@router.post("/config/holidays/seed", status_code=status.HTTP_202_ACCEPTED)
async def seed_holidays(
    payload: HolidaySeedRequest, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> dict[str, int]:
    if payload.year_to < payload.year_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_range", "detail": "year_to < year_from", "status": 400},
        )
    rows: list[tuple[date, str, str, bool]] = []
    for year in range(payload.year_from, payload.year_to + 1):
        for fecha, descripcion in colombian_holidays(year).items():
            rows.append((fecha, descripcion, "festivo", True))
    repo = ConfigRepository(conn)
    created = await repo.bulk_insert_holidays(rows)
    return {"created": created}


# --- días hábiles -----------------------------------------------------------
@router.get("/business-days/calculate", response_model=BusinessDaysResult)
async def calculate_business_days(
    from_date: date = Query(..., alias="from"),
    days: int = Query(..., ge=0, le=3650),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BusinessDaysResult:
    repo = ConfigRepository(conn)
    # Ventana amplia para cubrir fines de semana y festivos del intervalo.
    window_end = from_date + timedelta(days=days * 2 + 60)
    non_business = await repo.non_business_dates(from_date, window_end)
    due = add_business_days(from_date, days, non_business)
    return BusinessDaysResult(from_date=from_date, business_days=days, due_date=due)


# --- parámetros -------------------------------------------------------------
@router.get("/config/params")
async def get_params(conn: asyncpg.Connection = Depends(get_tenant_conn)) -> dict[str, object]:
    repo = ConfigRepository(conn)
    return await repo.get_params()


@router.get("/config/params/{key}", response_model=ParamResponse)
async def get_param(key: str, conn: asyncpg.Connection = Depends(get_tenant_conn)) -> ParamResponse:
    repo = ConfigRepository(conn)
    value = await repo.get_param(key)
    if value is None and key not in await repo.get_params():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": f"Param '{key}' not found", "status": 404},
        )
    return ParamResponse(key=key, value=value)


@router.put("/config/params/{key}", response_model=ParamResponse)
async def set_param(
    key: str,
    payload: ParamUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ParamResponse:
    repo = ConfigRepository(conn)
    await repo.upsert_param(key, payload.value, _actor(request))
    return ParamResponse(key=key, value=payload.value)
