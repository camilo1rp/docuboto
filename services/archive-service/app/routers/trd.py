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
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.actor import Actor, require_actor
from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.archive import (
    ExpedienteDisposicionVencidaResponse,
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

# ---------------------------------------------------------------------------
# Qué se lee sin permiso, y por qué (decisión de 2026-08-28, §4.bis del plan de
# pruebas). Estas lecturas se dividen en dos clases que NO se pueden tratar
# igual:
#
#   1. El VOCABULARIO de clasificación -- el listado de series TRD, su detalle y
#      el cálculo de retención. Queda abierto a cualquier usuario autenticado
#      del tenant, y no por descuido: `/radicar` y `/expedientes` lo consumen
#      para poder clasificar, así que exigir `USUA_PERM_TRD` dejaría sin
#      radicar a todo operador ordinario -- el permiso lo tiene quien
#      ADMINISTRA el instrumento, no quien lo usa. Además el instrumento
#      convalidado es de publicación obligatoria (Ac. AGN 004/2019, compilado
#      por 001/2024; Ley 1712/2014 art. 9), así que la apertura es coherente
#      con la norma. Esto es una decisión declarada, no una omisión.
#
#   2. El CIRCUITO -- `GET /{code}/versiones`, que devuelve el historial
#      completo: los borradores, el `acta_comite`, la `fecha_aprobacion_comite`,
#      la `instancia_convalidante` y el `created_by` de cada versión. Eso es la
#      deliberación del Comité Institucional de Gestión y Desempeño, no el
#      instrumento publicado, y solo lo consume `/admin/trd`. Exige
#      `USUA_PERM_TRD`, igual que su gemelo `GET /revision-pendiente`, que ya lo
#      exigía: era el hueco que quedaba de ese mismo par.
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/trd", tags=["trd"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "Falta la cabecera X-Tenant-Slug",
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
                "detail": "La cabecera X-User-Id debe ser un UUID válido",
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
    try:
        return await service.create_trd_serie(payload, actor.id)
    except asyncpg.UniqueViolationError:
        # `trd_series_code_version_key`: el código ya existe. Sin esto salía un
        # **500** —«Ocurrió un error inesperado»— por un dato que la persona
        # puede corregir sola, y sin decirle cuál era el problema.
        #
        # Su gemelo `tipos_documentales.py::create_tipo` sí lo traduce, sobre el
        # mismo tipo de conflicto y en el mismo servicio: es omisión, no diseño.
        # Lo destapó el guion de demostración al ejecutarse dos veces seguidas,
        # que es lo primero que hace cualquiera.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"Ya existe una serie con el código '{payload.code}'",
                "status": 409,
            },
        )


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
    # ADR-026 (Increment 2, aditivo): filtro opcional por instrumento --
    # sin él, el informe mezcla TRD/TVD (hoy solo TRD la genera en la
    # práctica, ver ADR-026 §"Máquina de estados").
    tipo: Literal["TRD", "TVD"] | None = Query(default=None),
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
        page=page, size=size, user_id=user_id, canal=canal, tipo=tipo
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/disposicion-vencida",
    response_model=list[ExpedienteDisposicionVencidaResponse],
    summary="Expedientes cuyo calendario de retención ya venció (RF-TRD-08)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def list_disposicion_vencida(
    response: Response,
    fase: Literal["gestion", "central"] = Query(
        default="central",
        description=(
            "'central': venció fin_archivo_central, toca la DISPOSICIÓN FINAL. "
            "'gestion': venció fin_archivo_gestion, toca transferir al archivo central."
        ),
    ),
    disposition_code: Literal["CT", "E", "S", "M"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    # Mismas dos dependencias que `revision-pendiente`, y por el mismo motivo:
    # `USUA_PERM_TRD` es ORTOGONAL al clearance, así que sin resolverlo este
    # informe enumeraría expedientes RESERVADA/CLASIFICADA a cualquier
    # portador del permiso.
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteDisposicionVencidaResponse]:
    # Declarado ANTES de `GET /{serie_id}`, igual que `revision-pendiente`:
    # ambas son plantillas de UN segmento y Starlette resuelve por orden de
    # registro -- si `/{serie_id}` fuera primero, "disposicion-vencida" caería
    # ahí y reventaría con un 422 al no parsear como UUID.
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_expedientes_disposicion_vencida(
        page=page,
        size=size,
        fase=fase,
        disposition_code=disposition_code,
        user_id=user_id,
        canal=canal,
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
    # ADR-026 (Increment 2, D4): `closed_at` sigue siendo el nombre del
    # parámetro y el `base_date_origen` de esta ruta -- es la semántica TRD.
    # El gemelo para TVD es `GET /api/v1/tvd/{id}/retention?base_date=`
    # (`app.routers.tvd`), con `base_date_origen="fecha_extrema_final"`
    # fijado por el router, nunca aceptado del cliente.
    service = ArchiveService(conn, tenant_slug)
    return await service.compute_retention(
        serie_id, closed_at, base_date_origen="closed_at"
    )


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
    # Circuito, no vocabulario: ver la declaración al principio del módulo.
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
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
