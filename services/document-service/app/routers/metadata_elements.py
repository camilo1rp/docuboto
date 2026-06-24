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
"""

import json
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.database import get_tenant_conn
from app.repositories.metadata_repository import MetadataElementRepository
from app.schemas.metadata import (
    MetadataElementCreate,
    MetadataElementResponse,
    MetadataElementUpdate,
)

router = APIRouter(prefix="/api/v1/metadata/elements", tags=["metadata"])


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
)
async def create_element(
    payload: MetadataElementCreate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> MetadataElementResponse:
    repo = MetadataElementRepository(conn)
    try:
        row = await repo.insert(payload.model_dump())
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


@router.patch("/{element_id}", response_model=MetadataElementResponse)
async def update_element(
    element_id: uuid.UUID,
    payload: MetadataElementUpdate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> MetadataElementResponse:
    repo = MetadataElementRepository(conn)
    row = await repo.update(element_id, payload.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Elemento no encontrado", "status": 404},
        )
    return _to_response(row)


@router.delete("/{element_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_element(
    element_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    if not await MetadataElementRepository(conn).delete(element_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Elemento no encontrado", "status": 404},
        )
