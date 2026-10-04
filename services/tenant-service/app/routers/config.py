# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de configuración por tenant (E14): festivos, días hábiles, parámetros.

Las escrituras exigen `USUA_PERM_ADMIN` (`app/core/authz.py`). Los GET de festivos
y `business-days/calculate` quedan solo autenticados: el cálculo de plazos legales
de respuesta los consume para cualquier usuario radicador, no solo administradores.
"""

import uuid
from datetime import date, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core import audit as audit_core
from app.core.authz import require_permission
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


# --- festivos ---------------------------------------------------------------
@router.get("/config/holidays", response_model=list[HolidayResponse])
async def list_holidays(
    year: int | None = Query(None, ge=2000, le=2100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[HolidayResponse]:
    repo = ConfigRepository(conn)
    return [HolidayResponse(**dict(r)) for r in await repo.list_holidays(year)]


@router.post(
    "/config/holidays",
    response_model=HolidayResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def create_holiday(
    payload: HolidayCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> HolidayResponse:
    repo = ConfigRepository(conn)
    try:
        async with conn.transaction():
            row = await repo.insert_holiday(
                fecha=payload.fecha,
                descripcion=payload.descripcion,
                tipo=payload.tipo,
                is_national=payload.is_national,
            )
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="config.festivo_creado",
                object_type="festivo",
                object_ref=str(payload.fecha),
                actor=audit_core.actor_from(request),
                payload={"descripcion": payload.descripcion, "tipo": payload.tipo},
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"Ya existe un día no hábil registrado para el {payload.fecha}",
                "status": 409,
            },
        )
    return HolidayResponse(**dict(row))


@router.delete(
    "/config/holidays/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def delete_holiday(
    holiday_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = ConfigRepository(conn)
    async with conn.transaction():
        if not await repo.delete_holiday(holiday_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "detail": "No existe el día no hábil", "status": 404},
            )
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="config.festivo_eliminado",
            object_type="festivo",
            object_ref=str(holiday_id),
            actor=audit_core.actor_from(request),
            canal=audit_core.CANAL_PETICION,
        )


@router.post(
    "/config/holidays/seed",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def seed_holidays(
    payload: HolidaySeedRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
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
    async with conn.transaction():
        created = await repo.bulk_insert_holidays(rows)
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="config.festivos_sembrados",
            object_type="festivo",
            object_ref=f"{payload.year_from}-{payload.year_to}",
            actor=audit_core.actor_from(request),
            payload={"created": created},
            canal=audit_core.CANAL_PETICION,
        )
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
            detail={"error": "not_found", "detail": f"No existe el parámetro «{key}»", "status": 404},
        )
    return ParamResponse(key=key, value=value)


@router.put(
    "/config/params/{key}",
    response_model=ParamResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def set_param(
    key: str,
    payload: ParamUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ParamResponse:
    repo = ConfigRepository(conn)
    actor = audit_core.actor_from(request)
    async with conn.transaction():
        # `upsert_param` ya escribe `admin_audit` (columna `updated_by`, por-tenant,
        # no inmutable); este `append` añade la traza Canal A (`public.audit_log`,
        # cadena de hash) en la MISMA transacción.
        existia, antes = await repo.upsert_param(key, payload.value, actor)
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="config.parametro_actualizado",
            object_type="parametro",
            object_ref=key,
            actor=actor,
            # `antes` + `despues`: sin el valor previo, el asiento solo dice cómo
            # quedó el parámetro, no qué estaba vigente hasta ese momento.
            payload={
                "value": payload.value,
                "antes": antes,
                "despues": payload.value,
                "existia": existia,
            },
            canal=audit_core.CANAL_PETICION,
        )
    return ParamResponse(key=key, value=payload.value)
