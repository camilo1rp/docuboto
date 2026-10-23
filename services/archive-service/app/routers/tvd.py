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
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.actor import Actor, require_actor
from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.archive import (
    RetentionSchedule,
    TrdSerieAprobar,
    TrdSerieConvalidar,
    TrdSerieDevolver,
    TrdSerieRegistrarRusd,
    TrdSerieResponse,
    TvdAgrupacionCreate,
    TvdAgrupacionUpdate,
    TvdAgrupacionVersionCreate,
)
from app.schemas.retencion_legal import (
    SeleccionPendienteItem,
    SeleccionValoracionRequest,
    SeleccionValoracionResponse,
    ValoracionTvdEstado,
    ValoracionTvdRequest,
    ValoracionTvdResponse,
)
from app.services.archive_service import ArchiveService
from app.services.valoracion_service import ValoracionService

# ---------------------------------------------------------------------------
# Qué se lee sin permiso, y por qué (decisión de 2026-08-28, §4.bis del plan de
# pruebas). Estas lecturas se dividen en dos clases que NO se pueden tratar
# igual:
#
#   1. El VOCABULARIO de clasificación -- el listado de agrupaciones TVD, su detalle y
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
#      instrumento publicado, y solo lo consume `/admin/tvd`. Exige
#      `USUA_PERM_TRD`, igual que su gemelo `GET /revision-pendiente`, que ya lo
#      exigía: era el hueco que quedaba de ese mismo par.
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/tvd", tags=["tvd"])


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


@router.get(
    "/{agrupacion_id}/retention",
    response_model=RetentionSchedule,
    summary="Compute retention/disposition schedule for a fondo acumulado agrupacion (ADR-026, Increment 2)",
)
async def compute_retention(
    agrupacion_id: uuid.UUID,
    base_date: date = Query(
        ...,
        description=(
            "Fecha extrema final del fondo acumulado (ISO date) -- NUNCA la "
            "fecha de cierre del expediente (ADR-026 D4). Obligatoria y "
            "explícita: sin default a hoy."
        ),
    ),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RetentionSchedule:
    # `base_date_origen` fijado aquí, nunca aceptado del cliente -- mismo
    # criterio que `tipo_instrumento` en el resto de este router (ADR-026
    # §"Superficie de API").
    service = ArchiveService(conn, tenant_slug)
    return await service.compute_retention(
        agrupacion_id, base_date, base_date_origen="fecha_extrema_final"
    )


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
    # Circuito, no vocabulario: ver la declaración al principio del módulo.
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
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


@router.get(
    "/{serie_id}/valoracion",
    response_model=ValoracionTvdEstado,
    summary="Valoración del Comité sobre una versión de TVD y avance de su aplicación (ADR-043)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def get_valoracion_tvd(
    serie_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ValoracionTvdEstado:
    return await ValoracionService(conn, tenant_slug).get_estado(serie_id)


@router.post(
    "/{serie_id}/valoracion",
    response_model=ValoracionTvdResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Registrar la valoración del Comité sobre una versión de TVD aprobada o convalidada: "
        "levanta la retención legal 'valoracion_pendiente' de los expedientes del fondo (ADR-043)"
    ),
    # Mismo cargo que la TVD (ADR-026 §permiso): es el acto del Comité sobre el instrumento.
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def registrar_valoracion_tvd(
    serie_id: uuid.UUID,
    payload: ValoracionTvdRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ValoracionTvdResponse:
    """Respuesta: solo el RECUENTO de expedientes afectados (acto de custodia, ortogonal al
    clearance: no enumera expedientes). 409 `tvd_no_aprobada` si la versión no está aprobada ni
    convalidada; 409 `valoracion_ya_registrada` (una por versión)."""
    return await ValoracionService(conn, tenant_slug).registrar_valoracion(
        serie_id, payload, actor=actor.id
    )


@router.get(
    "/{serie_id}/valoracion/seleccion-pendiente",
    response_model=list[SeleccionPendienteItem],
    summary="Expedientes de una valoración de Selección (S) que esperan el acto de selección",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def listar_seleccion_pendiente(
    serie_id: uuid.UUID,
    response: Response,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[SeleccionPendienteItem]:
    """Solo identificador y código del expediente (ni título ni contenido): es la cola del acto
    de selección, no una lectura del expediente. `X-Total-Count` lleva el total."""
    items, total = await ValoracionService(conn, tenant_slug).listar_seleccion_pendiente(
        serie_id, page=page, size=size
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post(
    "/{serie_id}/valoracion/expedientes/{expediente_id}/seleccion",
    response_model=SeleccionValoracionResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Acto de selección (disposición S) sobre UN expediente: `conservar` sigue el camino de "
        "CT; `descartar` levanta la retención legal y lo deja elegible para el lote (ADR-043)"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def registrar_seleccion_valoracion(
    serie_id: uuid.UUID,
    expediente_id: uuid.UUID,
    payload: SeleccionValoracionRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> SeleccionValoracionResponse:
    """404 `valoracion_expediente_not_found` si el expediente no está en esa versión; 409
    `valoracion_no_es_seleccion` si la valoración no es S; 409 `seleccion_ya_registrada` (el acto
    no se rehace). Atómico con su asiento `archive.expediente_seleccion_registrada`."""
    return await ValoracionService(conn, tenant_slug).registrar_seleccion(
        serie_id, expediente_id, payload, actor=actor.id
    )
