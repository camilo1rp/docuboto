# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.actor import Actor, require_actor
from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.archive import (
    ExpedienteRevisionPendienteResponse,
    RetentionSchedule,
    TrdSerieAprobar,
    TrdSerieConvalidar,
    TrdSerieCreate,
    TrdSerieDevolver,
    TrdSerieRegistrarRusd,
    TrdSerieResponse,
    TrdSerieUpdate,
    TrdSerieVersionCreate,
)
from app.services.archive_service import ArchiveService

router = APIRouter(prefix="/api/v1/trd", tags=["trd"])


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


def _optional_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    """Gemelo de `expedientes.py::_optional_user` (migración 038): resuelve
    el clearance del llamante para `GET /trd/revision-pendiente` -- distinto
    de `Actor`/`require_actor` (que exige X-User-Id fail-closed para
    MUTACIONES), este endpoint es una LECTURA y `X-User-Id` ausente solo
    significa "no auditar como consulta de un usuario real" (cae a
    PUBLICA), nunca un 400."""
    if not x_user_id:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "X-User-Id must be a valid UUID",
                "status": 400,
            },
        )


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Gemelo de `expedientes.py::_optional_canal` (E15 perfil v4, R3-a)."""
    return x_canal or "system"


@router.post(
    "",
    response_model=TrdSerieResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a TRD documentary series",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def create_trd_serie(
    payload: TrdSerieCreate,
    tenant_slug: str = Depends(_require_tenant),
    # El endpoint ya está gateado por `USUA_PERM_TRD`, así que siempre hay un
    # usuario autenticado — `X-User-Id` ausente es una cabecera perdida, no un
    # caso legítimo (fail-closed 400, mismo helper único que expedientes.py/
    # transferencias.py, ver `app.core.actor`).
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.create_trd_serie(payload, actor.id)


@router.get(
    "",
    response_model=list[TrdSerieResponse],
    summary="List TRD series",
)
async def list_trd_series(
    response: Response,
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TrdSerieResponse]:
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_trd_series(is_active=is_active, page=page, size=size)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/revision-pendiente",
    response_model=list[ExpedienteRevisionPendienteResponse],
    summary="List expedientes marked trd_revision_requerida (ADR-025 D4/D6)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def list_revision_pendiente(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    # Migración 038 (hallazgo de seguridad [Alta, BLOQUEANTE]): este informe
    # NO filtraba por clearance -- `USUA_PERM_TRD` (gate arriba) es
    # ORTOGONAL al clearance. Se añaden estas dos dependencias para poder
    # resolver `max_clearance` en el servicio, mismo patrón que
    # `expedientes.py::list_expedientes`.
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteRevisionPendienteResponse]:
    # Declarado ANTES de `GET /{serie_id}` a propósito: ambas son plantillas
    # de UN segmento y Starlette resuelve por orden de registro -- si
    # `/{serie_id}` fuera primero, "revision-pendiente" caería ahí y
    # reventaría con un 422 al no parsear como UUID.
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_expedientes_revision_pendiente(
        page=page, size=size, user_id=user_id, canal=canal
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{serie_id}/retention",
    response_model=RetentionSchedule,
    summary="Compute retention/disposition schedule for a closed expediente (E04)",
)
async def compute_retention(
    serie_id: uuid.UUID,
    closed_at: date = Query(..., description="Fecha de cierre del expediente (ISO date)"),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RetentionSchedule:
    service = ArchiveService(conn, tenant_slug)
    return await service.compute_retention(serie_id, closed_at)


@router.get(
    "/{serie_id}",
    response_model=TrdSerieResponse,
    summary="Get TRD serie by ID",
)
async def get_trd_serie(
    serie_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_trd_serie(serie_id)


@router.patch(
    "/{serie_id}",
    response_model=TrdSerieResponse,
    summary="Update TRD serie (append-only: only 'borrador'; use /versiones otherwise)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def update_trd_serie(
    serie_id: uuid.UUID,
    payload: TrdSerieUpdate,
    tenant_slug: str = Depends(_require_tenant),
    # Ídem `create_trd_serie`: mutación exige actor fail-closed.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.update_trd_serie(serie_id, payload, actor.id)


@router.post(
    "/{code}/versiones",
    response_model=TrdSerieResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft version of a TRD serie (ADR-025, append-only)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def create_trd_serie_version(
    code: str,
    payload: TrdSerieVersionCreate,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.create_trd_serie_version(code, payload, actor.id)


@router.get(
    "/{code}/versiones",
    response_model=list[TrdSerieResponse],
    summary=(
        "Full version history of a TRD serie code (ADR-026: gemelo que "
        "ADR-025 declaro aditivo y nunca implemento)"
    ),
)
async def list_trd_serie_versiones(
    code: str,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TrdSerieResponse]:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_trd_serie_versiones(code)


# ---------------------------------------------------------------------------
# Circuito de convalidación (ADR-025 D2/D6, Increment 2): aprobar / convalidar
# / derogar. Mismo permiso/min_crud que PATCH y POST /versiones (mutaciones
# del instrumento) -- ver `ArchiveService` para la máquina de estados.
# ---------------------------------------------------------------------------


@router.post(
    "/{code}/versiones/{version}/aprobar",
    response_model=TrdSerieResponse,
    summary=(
        "Approve a draft TRD serie version: borrador -> aprobada, records the "
        "Comite act (acta_comite/fecha_aprobacion_comite) -- ADR-025, migracion 038"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def aprobar_trd_serie_version(
    code: str,
    version: int,
    payload: TrdSerieAprobar,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.aprobar_trd_serie_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/devolver",
    response_model=TrdSerieResponse,
    summary=(
        "Return a version with observations: aprobada -> borrador, motivo "
        "required (ADR-025, migracion 038, hallazgo #1)"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def devolver_trd_serie_version(
    code: str,
    version: int,
    payload: TrdSerieDevolver,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.devolver_trd_serie_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/registrar-rusd",
    response_model=TrdSerieResponse,
    summary=(
        "Register RUSD inscription and/or publication date on an already-"
        "convalidada version (ADR-025, migracion 038, hallazgo #4)"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def registrar_rusd_trd_serie_version(
    code: str,
    version: int,
    payload: TrdSerieRegistrarRusd,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.registrar_rusd_trd_serie_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/convalidar",
    response_model=TrdSerieResponse,
    summary=(
        "Convalidate a TRD serie version: aprobada -> convalidada (deroga la "
        "vigente previa) o migrada -> convalidada (ratificacion D6, sin "
        "version nueva) -- ADR-025"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def convalidar_trd_serie_version(
    code: str,
    version: int,
    payload: TrdSerieConvalidar,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.convalidar_trd_serie_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/derogar",
    response_model=TrdSerieResponse,
    summary="Derogate a TRD serie version: convalidada/migrada -> derogada (ADR-025)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def derogar_trd_serie_version(
    code: str,
    version: int,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.derogar_trd_serie_version(code, version, actor.id)
