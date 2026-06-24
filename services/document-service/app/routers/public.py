# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Consulta pública por código de verificación (E13, F2).

Endpoint SIN autenticación: un ciudadano consulta la trazabilidad básica de su
radicado con un código no adivinable. El tenant viaja en la ruta (no hay JWT del
que extraerlo); el slug se valida y se fija el search_path al schema del tenant
(ADR-002/012). Solo se expone información NO sensible (sin datos personales ni
contenido del documento).
"""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.database import (
    get_db_conn,
    get_redis,
    get_tenant_client,
    tenant_schema,
)
from app.schemas.document import DocumentCreate
from app.schemas.public import PqrsCreate, PqrsResponse, PublicVerifyResponse
from app.services.document_service import DocumentService, InvalidTenantError

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.post(
    "/{tenant}/pqrs",
    response_model=PqrsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Radicación ciudadana de PQRS (público, Ley 1755/2015)",
)
async def radicar_pqrs(
    tenant: str,
    payload: PqrsCreate,
    conn: asyncpg.Connection = Depends(get_db_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> PqrsResponse:
    schema = tenant_schema(tenant)  # valida el slug
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        try:
            service = DocumentService(conn, tenant, tenant_client, redis)
        except InvalidTenantError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
            )
        doc = DocumentCreate(
            doc_type="E",
            subject=payload.asunto,
            sender_name=payload.nombre,
            observations=payload.descripcion,
            metadata={
                "canal": "pqrs_ciudadano",
                "tipo_pqrs": payload.tipo,
                "ciudadano": {
                    "nombre": payload.nombre,
                    "identificacion": payload.identificacion,
                    "email": payload.email,
                },
            },
        )
        result = await service.create(doc, registered_by=None)
        code = await conn.fetchval(
            "SELECT verification_code FROM radicados WHERE id = $1", result.id
        )
    finally:
        await conn.execute("SET search_path = public")
    return PqrsResponse(
        radicado_id=result.id,
        tracking_number=result.tracking_number,
        verification_code=code,
    )


@router.get("/{tenant}/verify/{code}", response_model=PublicVerifyResponse)
async def verify_radicado(
    tenant: str,
    code: str,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PublicVerifyResponse:
    schema = tenant_schema(tenant)  # valida el slug (anti-inyección)
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        row = await conn.fetchrow(
            "SELECT tracking_number, doc_type, status, registered_at, dest_dept "
            "FROM radicados WHERE verification_code = $1",
            code,
        )
    except (
        asyncpg.exceptions.UndefinedTableError,
        asyncpg.exceptions.InvalidSchemaNameError,
    ):
        row = None  # tenant inexistente → tratar como no encontrado (sin filtrar)
    finally:
        await conn.execute("SET search_path = public")

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "Código de verificación no encontrado",
                "status": 404,
            },
        )
    return PublicVerifyResponse(**dict(row))
