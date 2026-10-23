# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Catálogo de tipos documentales (3.er nivel TRD, E03/RF-MET-07).

CRUD de `tipos_documentales`: el tipo cuelga de una serie TRD, se marca
obligatorio/opcional y referencia la plantilla de metadatos asociada.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from orpycamcp_common import audit

from app.core.actor import Actor, require_actor
from app.core.clearance import (
    check_permission,
    mensaje_denegacion,
    nivel_efectivo,
    require_permission,
)
from app.core.database import get_db_conn
from app.repositories.archive_repository import ArchiveRepository
from app.schemas.archive import (
    TipoDocumentalCreate,
    TipoDocumentalResponse,
    TipoDocumentalUpdate,
)
from app.services.tipo_documental_service import TipoDocumentalService

router = APIRouter(prefix="/api/v1/tipos-documentales", tags=["tipos-documentales"])


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


async def _exigir_nivel_difusion(
    conn: asyncpg.Connection, tenant_slug: str, actor: Actor
) -> None:
    """Cambiar `publicable` es una decisión de DIFUSIÓN a internet (Ley 1712/2014 art. 12):
    exige `USUA_PERM_TRD` nivel 3, no el nivel con el que se edita el resto del catálogo
    (C-4/C10). La conexión ya trae el `search_path` del tenant (lo fijó el gate de ruta)."""
    if await check_permission(conn, actor.id, "USUA_PERM_TRD", 3):
        return
    try:  # traza best-effort de la denegación, nunca un 500 por auditoría
        await audit.append_denial(
            conn,
            tenant_slug=tenant_slug,
            service="archive-service",
            actor=str(actor.id),
            perm_name="USUA_PERM_TRD",
            min_crud=3,
        )
    except Exception:  # noqa: BLE001
        pass
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "forbidden",
            "detail": mensaje_denegacion(
                "USUA_PERM_TRD", 3, await nivel_efectivo(conn, actor.id, "USUA_PERM_TRD")
            ),
            "status": 403,
        },
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": "Tipo documental no encontrado", "status": 404},
    )


@router.post(
    "",
    response_model=TipoDocumentalResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def create_tipo(
    payload: TipoDocumentalCreate,
    tenant_slug: str = Depends(_require_tenant),
    # Gateado por `USUA_PERM_TRD`: siempre hay un usuario autenticado, así que
    # `X-User-Id` ausente es una cabecera perdida, no un caso legítimo
    # (fail-closed 400, mismo helper único que `trd.py`/expedientes.py).
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TipoDocumentalResponse:
    if payload.publicable:
        await _exigir_nivel_difusion(conn, tenant_slug, actor)
    service = TipoDocumentalService(conn, tenant_slug)
    try:
        return await service.create(payload.model_dump(), actor=actor.id)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"Ya existe el tipo '{payload.code}' en esa serie",
                "status": 409,
            },
        )


@router.get("", response_model=list[TipoDocumentalResponse])
async def list_tipos(
    trd_serie_id: uuid.UUID | None = Query(default=None),
    activo: bool | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TipoDocumentalResponse]:
    repo = ArchiveRepository(conn, tenant_slug)
    rows = await repo.list_tipos_documentales(trd_serie_id=trd_serie_id, activo=activo)
    return [TipoDocumentalResponse(**dict(r)) for r in rows]


@router.get("/{tipo_id}", response_model=TipoDocumentalResponse)
async def get_tipo(
    tipo_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TipoDocumentalResponse:
    row = await ArchiveRepository(conn, tenant_slug).get_tipo_documental(tipo_id)
    if row is None:
        raise _not_found()
    return TipoDocumentalResponse(**dict(row))


@router.patch(
    "/{tipo_id}",
    response_model=TipoDocumentalResponse,
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def update_tipo(
    tipo_id: uuid.UUID,
    payload: TipoDocumentalUpdate,
    tenant_slug: str = Depends(_require_tenant),
    # Ídem `create_tipo`: mutación exige actor fail-closed.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TipoDocumentalResponse:
    cambios = payload.model_dump(exclude_unset=True)
    service = TipoDocumentalService(conn, tenant_slug)
    # La decisión de «¿esto cambia la difusión?» se toma en el servicio, sobre la fila BLOQUEADA
    # (`FOR UPDATE`): decidirla aquí con una lectura suelta dejaba una carrera entre dos PATCH.
    row = await service.update(
        tipo_id,
        cambios,
        actor=actor.id,
        exigir_difusion=lambda: _exigir_nivel_difusion(conn, tenant_slug, actor),
    )
    if row is None:
        raise _not_found()
    return row


@router.delete(
    "/{tipo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def delete_tipo(
    tipo_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    # Ídem `create_tipo`: mutación exige actor fail-closed.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    if not await TipoDocumentalService(conn, tenant_slug).delete(tipo_id, actor=actor.id):
        raise _not_found()
