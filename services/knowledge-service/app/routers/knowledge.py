# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Capa de conocimiento: ingesta y recuperación semántica con pre-filtrado ACL (E21)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core.clearance import resolve_caller_clearance
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


class IngestRequest(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=40)
    source_ref: str = Field(..., min_length=1)
    texto: str = Field(..., min_length=1)
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


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest(
    payload: IngestRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> IngestResponse:
    literal = to_pgvector(embed(payload.texto))
    row = await KnowledgeRepository(conn).insert_chunk(
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        texto=payload.texto,
        embedding_literal=literal,
        metadata=payload.metadata,
        acl=payload.acl,
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
