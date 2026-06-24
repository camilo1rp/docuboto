# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Router de plantillas de metadatos del expediente (E03, ADR-007).

CRUD mínimo de `expediente_metadata_templates`: el JSON Schema de los metadatos
válidos por serie TRD. Los expedientes se validan contra la plantilla activa de
su serie al crearse (archive_service).
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.database import get_db_conn
from app.schemas.metadata import ExpedienteTemplateCreate, ExpedienteTemplateResponse
from app.services.metadata_service import (
    ExpedienteTemplateService,
    MetadataValidationError,
)

router = APIRouter(prefix="/api/v1/expediente-metadata/templates", tags=["metadata"])


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
    response_model=ExpedienteTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una plantilla de metadatos para una serie TRD",
)
async def create_template(
    payload: ExpedienteTemplateCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteTemplateResponse:
    service = ExpedienteTemplateService(conn, tenant_slug)
    try:
        return await service.create(payload)
    except MetadataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_json_schema", "detail": str(exc), "status": 422},
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": "Ya existe esa versión o una plantilla activa para la serie",
                "status": 409,
            },
        )


@router.get(
    "",
    response_model=list[ExpedienteTemplateResponse],
    summary="Listar plantillas (filtro opcional por serie TRD)",
)
async def list_templates(
    trd_serie_id: uuid.UUID | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteTemplateResponse]:
    service = ExpedienteTemplateService(conn, tenant_slug)
    return await service.list(trd_serie_id=trd_serie_id)
