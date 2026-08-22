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
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from app.core.clearance import require_permission
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
    UbicacionUpdate,
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
                "detail": "Falta la cabecera X-Tenant-Slug",
                "status": 400,
            },
        )
    return x_tenant_slug


def _optional_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    """X-User-Id como UUID, para resolver el clearance del llamante (RF-SEG-08).

    Gemelo de `expedientes.py::_optional_user` / `indice.py::_optional_user`:
    usado en el FUID (`/fuid`, `/fuid.xml`) y en `expedientes_in_unit`
    (`/unidades/{id}/expedientes`), que exponen asunto/serie/signatura o la
    mera existencia del expediente — contenido, no metadato de custodia.
    """
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


# -- ubicaciones -------------------------------------------------------


@router.post(
    "/ubicaciones",
    response_model=UbicacionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def create_ubicacion(
    payload: UbicacionCreate,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UbicacionResponse:
    return await FisicoService(conn, tenant_slug).create_ubicacion(payload, actor=actor)


@router.get(
    "/ubicaciones",
    response_model=list[UbicacionResponse],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_ubicaciones(
    parent_id: uuid.UUID | None = Query(default=None),
    activo: bool | None = Query(default=None),
    solo_raiz: bool = Query(
        default=False,
        description=(
            "Devuelve solo las ubicaciones de primer nivel (sin padre). "
            "Omitir `parent_id` NO significa eso: significa no filtrar."
        ),
    ),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[UbicacionResponse]:
    if solo_raiz and parent_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "filtros_incompatibles",
                "detail": "`solo_raiz` y `parent_id` se excluyen: o el primer nivel, o los hijos de una ubicación.",
                "status": 422,
            },
        )
    return await FisicoService(conn, tenant_slug).list_ubicaciones(
        parent_id=parent_id, activo=activo, solo_raiz=solo_raiz
    )


@router.patch(
    "/ubicaciones/{ubicacion_id}",
    response_model=UbicacionResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
    summary="Corrige nombre, tipo de archivo, capacidad o estado de una ubicación",
)
async def update_ubicacion(
    ubicacion_id: uuid.UUID,
    payload: UbicacionUpdate,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UbicacionResponse:
    """Corrección de una ubicación ya creada.

    Faltaba por completo, y el hueco no era teórico: `tipo_archivo` es opcional
    al crear y decide qué transferencias admite la ubicación (Ley 594/2000
    art. 23). Una topografía cargada sin ese dato dejaba el archivo físico sin
    poder transferir **y sin forma de arreglarlo desde el sistema**.

    `codigo` y `parent_id` quedan fuera a propósito: el primero es la
    referencia que las signaturas topográficas ya llevan impresas, y el segundo
    movería de golpe todo lo que cuelga de la ubicación. Exige `crud >= 3`
    porque corregir la topografía del depósito no es una escritura ordinaria.
    """
    return await FisicoService(conn, tenant_slug).update_ubicacion(
        ubicacion_id, payload, actor=actor
    )


@router.get(
    "/ubicaciones/{ubicacion_id}",
    response_model=UbicacionResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_ubicacion(
    ubicacion_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UbicacionResponse:
    return await FisicoService(conn, tenant_slug).get_ubicacion(ubicacion_id)


# -- unidades de conservación -----------------------------------------


@router.post(
    "/unidades",
    response_model=UnidadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def create_unidad(
    payload: UnidadCreate,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> UnidadResponse:
    return await FisicoService(conn, tenant_slug).create_unidad(payload, actor=actor)


@router.get(
    "/unidades",
    response_model=list[UnidadResponse],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_unidades(
    ubicacion_id: uuid.UUID | None = Query(default=None),
    tipo: str | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[UnidadResponse]:
    return await FisicoService(conn, tenant_slug).list_unidades(
        ubicacion_id=ubicacion_id, tipo=tipo
    )


@router.get(
    "/unidades/{unidad_id}",
    response_model=UnidadResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
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
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def link_expediente(
    unidad_id: uuid.UUID,
    payload: ExpedienteUnidadLink,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    await FisicoService(conn, tenant_slug).link_expediente(unidad_id, payload, actor=actor)


@router.get(
    "/unidades/{unidad_id}/expedientes",
    response_model=list[ExpedienteEnUnidad],
    summary="What does this unit contain",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def expedientes_in_unit(
    unidad_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteEnUnidad]:
    return await FisicoService(conn, tenant_slug).expedientes_in_unit(unidad_id, user_id=user_id)


@router.get(
    "/expedientes/{expediente_id}/unidades",
    response_model=list[UnidadDeExpediente],
    summary="Where is this expediente (physical units + signatura)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def units_of_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[UnidadDeExpediente]:
    # No-read-up DELIBERADAMENTE NO aplicado aquí (decisión arquitecto +
    # archival-compliance-auditor, RF-SEG-08): esta ruta se indexa por un
    # `expediente_id` (UUIDv4 no adivinable) YA CONOCIDO y solo revela DÓNDE
    # está físicamente (signatura + folios) — es GESTIÓN DE CUSTODIA ortogonal
    # al clearance de lectura (el operario de depósito localiza la unidad para
    # préstamo/movimiento sin habilitación para leer su contenido; exigir
    # clearance invertiría el mínimo privilegio, mismo criterio que la
    # transferencia/cierre en M1). NO expone asunto/serie/descripción, así que
    # no filtra CONTENIDO ni ENUMERA existencia. Contrasta con
    # `/unidades/{id}/expedientes` (gemela), que SÍ enumera los expedientes de
    # una unidad y por eso SÍ filtra por fila (Ley 1712/2014 art. 19).
    # Residual conocido (Baja): la ubicación física de un CLASIFICADA a quien
    # porta su UUID — seguimiento para el barrido conjunto con compliance.
    return await FisicoService(conn, tenant_slug).units_of_expediente(expediente_id)


# -- préstamos y movimientos (RF-ARF-07/08) ---------------------------


def _performed_by(request: Request) -> str | None:
    return request.headers.get("X-Username") or request.headers.get("X-User-Id")


@router.post(
    "/unidades/{unidad_id}/prestamos",
    response_model=PrestamoResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def create_prestamo(
    unidad_id: uuid.UUID,
    payload: PrestamoCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PrestamoResponse:
    return await FisicoService(conn, tenant_slug).create_prestamo(
        unidad_id, payload, _performed_by(request), actor=actor
    )


@router.get(
    "/unidades/{unidad_id}/prestamos",
    response_model=list[PrestamoResponse],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_prestamos_of_unit(
    unidad_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[PrestamoResponse]:
    return await FisicoService(conn, tenant_slug).list_prestamos(estado=None, unidad_id=unidad_id)


@router.get(
    "/prestamos",
    response_model=list[PrestamoResponse],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_prestamos(
    estado: str | None = Query(default=None, description="prestado|devuelto|vencido"),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[PrestamoResponse]:
    return await FisicoService(conn, tenant_slug).list_prestamos(estado=estado, unidad_id=None)


@router.post(
    "/prestamos/{prestamo_id}/devolver",
    response_model=PrestamoResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def devolver_prestamo(
    prestamo_id: uuid.UUID,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PrestamoResponse:
    return await FisicoService(conn, tenant_slug).devolver_prestamo(
        prestamo_id, _performed_by(request), actor=actor
    )


@router.get(
    "/unidades/{unidad_id}/movimientos",
    response_model=list[MovimientoResponse],
    summary="Append-only physical movement history of a unit (RF-ARF-08)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
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
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_fuid(
    ubicacion_id: uuid.UUID | None = Query(default=None),
    trd_serie_id: uuid.UUID | None = Query(default=None),
    # Campos declarativos de la cabecera del FUID (Ac. AGN 042/2002, Anexo).
    # Los rellena quien elabora el inventario, igual que en papel — el sistema no
    # los verifica y por eso no los inventa: un valor por defecto plausible sería
    # peor que un hueco, porque nadie lo revisaría. La `entidadProductora`, en
    # cambio, la resuelve el servidor desde `public.tenants` (11.11).
    unidad_administrativa: str | None = Query(default=None, max_length=200),
    oficina_productora: str | None = Query(default=None, max_length=200),
    objeto: str | None = Query(default=None, max_length=500),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FuidResponse:
    return await FisicoService(conn, tenant_slug).generate_fuid(
        ubicacion_id=ubicacion_id,
        trd_serie_id=trd_serie_id,
        user_id=user_id,
        unidad_administrativa=unidad_administrativa,
        oficina_productora=oficina_productora,
        objeto=objeto,
    )


@router.get(
    "/fuid.xml",
    summary="FUID export as XML (RF-ARF-10)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_fuid_xml(
    ubicacion_id: uuid.UUID | None = Query(default=None),
    trd_serie_id: uuid.UUID | None = Query(default=None),
    # Campos declarativos de la cabecera del FUID (Ac. AGN 042/2002, Anexo).
    # Los rellena quien elabora el inventario, igual que en papel — el sistema no
    # los verifica y por eso no los inventa: un valor por defecto plausible sería
    # peor que un hueco, porque nadie lo revisaría. La `entidadProductora`, en
    # cambio, la resuelve el servidor desde `public.tenants` (11.11).
    unidad_administrativa: str | None = Query(default=None, max_length=200),
    oficina_productora: str | None = Query(default=None, max_length=200),
    objeto: str | None = Query(default=None, max_length=500),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> Response:
    xml = await FisicoService(conn, tenant_slug).generate_fuid_xml(
        ubicacion_id=ubicacion_id,
        trd_serie_id=trd_serie_id,
        user_id=user_id,
        unidad_administrativa=unidad_administrativa,
        oficina_productora=oficina_productora,
        objeto=objeto,
    )
    return Response(content=xml, media_type="application/xml")
