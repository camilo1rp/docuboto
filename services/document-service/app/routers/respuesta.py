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
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.document import DocumentResponse
from app.schemas.respuesta import RespuestaItem, RespuestaRapidaCreate
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentService,
    InvalidDependenciaError,
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


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Canal de origen (X-Canal, inyectado server-side por el api-gateway, D-03;
    no forjable por el cliente). Ausente => 'system' (no se audita como consulta
    de un usuario real). Gemelo de `documents.py::_optional_canal`."""
    return x_canal or "system"


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
    dependencies=[Depends(require_permission("PERM_RESPUESTA_VINCULADA"))],
)
async def crear_respuesta(
    document_id: uuid.UUID,
    payload: RespuestaRapidaCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> DocumentResponse:
    """Crea una Salida vinculada a este radicado.

    No-read-up (RF-SEG-08): 404 neutro si el antecedente no existe o supera el
    clearance del llamante. La Salida hereda el asunto del antecedente, así que
    sin este gate el endpoint era una vía de exfiltración.
    """
    service = _service(conn, _slug(request), tenant_client, redis)
    clearance = await resolve_caller_clearance(conn, _user(request))
    try:
        return await service.crear_respuesta(document_id, payload, _user(request), clearance)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    except InvalidDependenciaError as exc:
        # El origen se hereda de la Entrada (Ac. 060 §5.2): si esa dependencia se
        # suprimió después, es un 400 explicable, no un 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_dependencia", "detail": str(exc), "status": 400},
        ) from exc


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
    canal: str = Depends(_optional_canal),
) -> list[RespuestaItem]:
    service = _service(conn, _slug(request), tenant_client, redis)
    user_id = _user(request)
    # No-read-up (RF-SEG-08): el clearance del llamante acota qué Salidas
    # vinculadas se listan (fail-closed a PUBLICA sin identidad).
    clearance = await resolve_caller_clearance(conn, user_id)
    return await service.listar_respuestas(
        document_id, max_clearance=clearance, user_id=user_id, canal=canal
    )
