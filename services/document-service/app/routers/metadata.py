# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Router de plantillas de metadatos (E03, ADR-007).

CRUD mínimo de `metadata_templates`: definir el JSON Schema de los campos de
metadato válidos por tipo documental. Los radicados se validan contra la
plantilla activa en su creación (document_service).
"""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.database import get_tenant_conn
from app.schemas.metadata import MetadataTemplateCreate, MetadataTemplateResponse
from app.services.metadata_service import (
    MetadataTemplateService,
    MetadataValidationError,
)

router = APIRouter(prefix="/api/v1/metadata/templates", tags=["metadata"])


@router.post(
    "",
    response_model=MetadataTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una plantilla de metadatos para un tipo documental",
)
async def create_template(
    payload: MetadataTemplateCreate,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> MetadataTemplateResponse:
    service = MetadataTemplateService(conn)
    try:
        return await service.create(payload)
    except MetadataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "invalid_json_schema",
                "detail": str(exc),
                "status": 422,
            },
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": (
                    f"Ya existe la plantilla {payload.tipo_documental} "
                    f"v{payload.version} (o ya hay una activa para el tipo)"
                ),
                "status": 409,
            },
        )


@router.get(
    "",
    response_model=list[MetadataTemplateResponse],
    summary="Listar plantillas de metadatos (filtro opcional por tipo documental)",
)
async def list_templates(
    tipo_documental: str | None = Query(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[MetadataTemplateResponse]:
    service = MetadataTemplateService(conn)
    return await service.list(tipo_documental=tipo_documental)
