# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Capa de conocimiento: ingesta y recuperación semántica con pre-filtrado ACL (E21).

D-02 (remediación): ingesta requiere X-Internal-Token (solo servicios internos); la
clasificación del chunk es obligatoria en la carga (derivada del documento origen);
material Reservado/Clasificado no puede ir a proveedores externos de embeddings.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.clearance import RESERVADA, resolve_caller_clearance
from app.core.config import settings
from app.core.database import get_tenant_conn
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embeddings import embed, to_pgvector

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _caller_id(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    if not x_user_id:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        return None


def _require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """D-02 (punto 4): solo servicios internos pueden ingestar chunks.

    Fail-closed: si INTERNAL_SERVICE_TOKEN no está configurado, todas las llamadas
    son rechazadas (401). Token presente pero incorrecto → 403.
    """
    configured = settings.internal_service_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "ingest_not_configured",
                "detail": "El servicio de ingesta no está habilitado (token sin configurar)",
                "status": 401,
            },
        )
    if x_internal_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_internal_token",
                "detail": "Se requiere el header X-Internal-Token para ingestar chunks",
                "status": 401,
            },
        )
    if x_internal_token != configured:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 403,
            },
        )


def _guard_external_llm(nivel_seguridad: int) -> None:
    """D-02 (punto 3): barrera dura — Reservado/Clasificado no sale a LLM externos.

    Material con nivel >= RESERVADA (2) sólo puede embeberse con el proveedor local
    (stub o modelo on-premise). Si está configurado un proveedor externo, la ingesta
    falla con 403 antes de llamar al proveedor.
    """
    if nivel_seguridad >= RESERVADA and settings.embedding_provider != "stub":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "classified_content_external_provider",
                "detail": (
                    "Material Reservado/Clasificado no puede procesarse con un "
                    "proveedor externo de embeddings (D-02 / RF-SEG-08)"
                ),
                "status": 403,
            },
        )


class IngestRequest(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=40)
    source_ref: str = Field(..., min_length=1)
    texto: str = Field(..., min_length=1)
    # D-02 (punto 2): obligatorio, sin default. El llamante DEBE derivarlo del
    # documento origen; no hay valor optimista (publica) por omisión.
    nivel_seguridad: int = Field(..., ge=1, le=3)
    metadata: dict[str, Any] = Field(default_factory=dict)
    acl: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    id: uuid.UUID
    source_type: str
    source_ref: str
    created_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    acl: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    id: uuid.UUID
    source_type: str
    source_ref: str
    texto: str
    metadata: dict[str, Any]
    score: float


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=201,
    dependencies=[Depends(_require_internal_token)],
)
async def ingest(
    payload: IngestRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> IngestResponse:
    # D-02 (punto 3): bloquea antes de llamar al proveedor de embeddings.
    _guard_external_llm(payload.nivel_seguridad)
    literal = to_pgvector(embed(payload.texto))
    row = await KnowledgeRepository(conn).insert_chunk(
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        texto=payload.texto,
        embedding_literal=literal,
        metadata=payload.metadata,
        acl=payload.acl,
        nivel_seguridad=payload.nivel_seguridad,
    )
    return IngestResponse(**dict(row))


@router.post("/search", response_model=list[SearchHit])
async def search(
    payload: SearchRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    caller_id: uuid.UUID | None = Depends(_caller_id),
) -> list[SearchHit]:
    literal = to_pgvector(embed(payload.query))
    clearance = await resolve_caller_clearance(conn, caller_id)
    rows = await KnowledgeRepository(conn).search(
        embedding_literal=literal,
        top_k=payload.top_k,
        max_clearance=clearance,
        acl_filter=payload.acl or None,
    )
    return [SearchHit(**dict(r)) for r in rows]
