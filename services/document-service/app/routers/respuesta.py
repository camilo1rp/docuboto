# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Respuesta rápida: crear una Salida vinculada a una Entrada (paridad legado)."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.document import DocumentResponse
from app.schemas.respuesta import RespuestaItem, RespuestaRapidaCreate
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentService,
    InvalidTenantError,
)

router = APIRouter(prefix="/api/v1/documents", tags=["respuesta"])

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"


def _slug(request: Request) -> str:
    return request.headers.get(_TENANT_HEADER, "")


def _user(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def _service(conn, slug, tenant_client, redis) -> DocumentService:
    try:
        return DocumentService(conn, slug, tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )


@router.post(
    "/{document_id}/respuesta",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Respuesta rápida: crear una Salida en respuesta a este radicado",
)
async def crear_respuesta(
    document_id: uuid.UUID,
    payload: RespuestaRapidaCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> DocumentResponse:
    service = _service(conn, _slug(request), tenant_client, redis)
    try:
        return await service.crear_respuesta(document_id, payload, _user(request))
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )


@router.get(
    "/{document_id}/respuestas",
    response_model=list[RespuestaItem],
    summary="Salidas que responden a este radicado",
)
async def listar_respuestas(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> list[RespuestaItem]:
    service = _service(conn, _slug(request), tenant_client, redis)
    return await service.listar_respuestas(document_id)
