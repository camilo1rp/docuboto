# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Catálogo de elementos de metadato (E03, RF-MET-03).

CRUD de `metadata_elements`: definiciones reutilizables de campos de metadato.
Las plantillas referencian estas claves en su JSON Schema.

Las escrituras (crear/actualizar/borrar) exigen `USUA_PERM_TRD` — mismo permiso
que gobierna las plantillas de metadatos (este router y su gemelo en
archive-service): son el mismo tipo de objeto de configuración de TRD/metadatos,
así que el modelo de permisos no se bifurca. El borrado además exige
`min_crud=3` (precedente: `archive-service/app/routers/tipos_documentales.py`,
`delete_tipo`) — retirar un elemento del catálogo es una operación más
destructiva que crearlo/editarlo. Las escrituras auditan (ver
`MetadataElementService` en `metadata_service.py`).
"""

import json
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.clearance import require_permission
from app.core.database import get_tenant_conn
from app.repositories.metadata_repository import MetadataElementRepository
from app.schemas.metadata import (
    MetadataElementCreate,
    MetadataElementResponse,
    MetadataElementUpdate,
)
from app.services.metadata_service import MetadataElementService

router = APIRouter(prefix="/api/v1/metadata/elements", tags=["metadata"])


def _actor(request: Request) -> str | None:
    raw = request.headers.get("X-User-Id")
    if not raw:
        return None
    try:
        uuid.UUID(raw)
    except ValueError:
        return None
    return raw


def _tenant_slug(request: Request) -> str:
    # get_tenant_conn ya garantiza que el header está presente y es válido.
    return request.headers.get("X-Tenant-Slug", "")


def _json(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


def _to_response(row: asyncpg.Record) -> MetadataElementResponse:
    data = dict(row)
    data["valor_default"] = _json(data.get("valor_default"))
    data["opciones"] = _json(data.get("opciones"))
    return MetadataElementResponse(**data)


@router.post(
    "",
    response_model=MetadataElementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un elemento de metadato",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def create_element(
    payload: MetadataElementCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> MetadataElementResponse:
    service = MetadataElementService(conn)
    try:
        row = await service.create(
            payload.model_dump(), tenant_slug=_tenant_slug(request), actor=_actor(request)
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"Ya existe un elemento con clave '{payload.clave}'",
                "status": 409,
            },
        )
    return _to_response(row)


@router.get("", response_model=list[MetadataElementResponse])
async def list_elements(
    activo: bool | None = Query(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[MetadataElementResponse]:
    repo = MetadataElementRepository(conn)
    return [_to_response(r) for r in await repo.list(activo=activo)]


@router.get("/{element_id}", response_model=MetadataElementResponse)
async def get_element(
    element_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> MetadataElementResponse:
    row = await MetadataElementRepository(conn).get(element_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Elemento no encontrado", "status": 404},
        )
    return _to_response(row)


@router.patch(
    "/{element_id}",
    response_model=MetadataElementResponse,
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def update_element(
    element_id: uuid.UUID,
    payload: MetadataElementUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> MetadataElementResponse:
    service = MetadataElementService(conn)
    row = await service.update(
        element_id,
        payload.model_dump(exclude_unset=True),
        tenant_slug=_tenant_slug(request),
        actor=_actor(request),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Elemento no encontrado", "status": 404},
        )
    return _to_response(row)


@router.delete(
    "/{element_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def delete_element(
    element_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    service = MetadataElementService(conn)
    if not await service.delete(
        element_id, tenant_slug=_tenant_slug(request), actor=_actor(request)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Elemento no encontrado", "status": 404},
        )
