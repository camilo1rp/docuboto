# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`/api/v1/tvd` (ADR-026, Increment 1): Tablas de Valoración Documental —
el MISMO instrumento que la TRD (discriminador `tipo_instrumento` sobre
`trd_series`, ver ADR-026 D1), con el mismo circuito de convalidación (Ac.
AGN 001/2024, que compila el 004/2019).

Espejo 1:1 de `app.routers.trd`, a propósito: cualquier asimetría entre las
dos superficies debe saltar a la vista en el diff. `tipo_instrumento` NUNCA
se acepta del cliente — lo fija esta capa (siempre 'TVD', vía los métodos
`*_tvd_agrupacion*`/`*_tvd_serie*` de `ArchiveService`, que a su vez
resuelven contra `v_tvd_agrupaciones`, nunca la tabla base). Permiso
reutilizado `USUA_PERM_TRD` (ADR-026 §"Superficie de API": misma potestad
archivística, mismo Comité — crear `USUA_PERM_TVD` exigiría una migración de
RBAC en auth-service por una distinción que la norma no hace)."""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.actor import Actor, require_actor
from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.archive import (
    TrdSerieAprobar,
    TrdSerieConvalidar,
    TrdSerieDevolver,
    TrdSerieRegistrarRusd,
    TrdSerieResponse,
    TvdAgrupacionCreate,
    TvdAgrupacionUpdate,
    TvdAgrupacionVersionCreate,
)
from app.services.archive_service import ArchiveService

router = APIRouter(prefix="/api/v1/tvd", tags=["tvd"])


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


@router.post(
    "",
    response_model=TrdSerieResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a TVD (Tabla de Valoracion Documental) agrupacion",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def create_tvd_agrupacion(
    payload: TvdAgrupacionCreate,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.create_tvd_agrupacion(payload, actor.id)


@router.get(
    "",
    response_model=list[TrdSerieResponse],
    summary="List TVD agrupaciones",
)
async def list_tvd_agrupaciones(
    response: Response,
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TrdSerieResponse]:
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_tvd_agrupaciones(is_active=is_active, page=page, size=size)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{agrupacion_id}",
    response_model=TrdSerieResponse,
    summary="Get TVD agrupacion by ID",
)
async def get_tvd_agrupacion(
    agrupacion_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_tvd_agrupacion(agrupacion_id)


@router.patch(
    "/{agrupacion_id}",
    response_model=TrdSerieResponse,
    summary="Update TVD agrupacion (append-only: only 'borrador'; use /versiones otherwise)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def update_tvd_agrupacion(
    agrupacion_id: uuid.UUID,
    payload: TvdAgrupacionUpdate,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.update_tvd_agrupacion(agrupacion_id, payload, actor.id)


@router.post(
    "/{code}/versiones",
    response_model=TrdSerieResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft version of a TVD agrupacion (ADR-026, append-only)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def create_tvd_agrupacion_version(
    code: str,
    payload: TvdAgrupacionVersionCreate,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.create_tvd_agrupacion_version(code, payload, actor.id)


@router.get(
    "/{code}/versiones",
    response_model=list[TrdSerieResponse],
    summary="Full version history of a TVD agrupacion code (ADR-026)",
)
async def list_tvd_agrupacion_versiones(
    code: str,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TrdSerieResponse]:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_tvd_agrupacion_versiones(code)


# ---------------------------------------------------------------------------
# Circuito de convalidación (ADR-026, mismo circuito que TRD -- Ac. AGN
# 001/2024, que compila el 004/2019): aprobar / devolver / convalidar /
# registrar-rusd / derogar.
# ---------------------------------------------------------------------------


@router.post(
    "/{code}/versiones/{version}/aprobar",
    response_model=TrdSerieResponse,
    summary=(
        "Approve a draft TVD agrupacion version: borrador -> aprobada, "
        "records the Comite act (acta_comite/fecha_aprobacion_comite) -- ADR-026"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def aprobar_tvd_agrupacion_version(
    code: str,
    version: int,
    payload: TrdSerieAprobar,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.aprobar_tvd_agrupacion_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/devolver",
    response_model=TrdSerieResponse,
    summary="Return a version with observations: aprobada -> borrador, motivo required (ADR-026)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def devolver_tvd_agrupacion_version(
    code: str,
    version: int,
    payload: TrdSerieDevolver,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.devolver_tvd_agrupacion_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/registrar-rusd",
    response_model=TrdSerieResponse,
    summary=(
        "Register RUSD inscription and/or publication date on an already-"
        "convalidada version (ADR-026)"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def registrar_rusd_tvd_agrupacion_version(
    code: str,
    version: int,
    payload: TrdSerieRegistrarRusd,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.registrar_rusd_tvd_agrupacion_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/convalidar",
    response_model=TrdSerieResponse,
    summary=(
        "Convalidate a TVD agrupacion version: aprobada -> convalidada "
        "(deroga la vigente previa) -- ADR-026"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def convalidar_tvd_agrupacion_version(
    code: str,
    version: int,
    payload: TrdSerieConvalidar,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.convalidar_tvd_agrupacion_version(code, version, payload, actor.id)


@router.post(
    "/{code}/versiones/{version}/derogar",
    response_model=TrdSerieResponse,
    summary="Derogate a TVD agrupacion version: convalidada -> derogada (ADR-026)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def derogar_tvd_agrupacion_version(
    code: str,
    version: int,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TrdSerieResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.derogar_tvd_agrupacion_version(code, version, actor.id)
