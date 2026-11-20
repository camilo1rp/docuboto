# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Búsqueda avanzada de radicados (E09, ADR-014: PostgreSQL FTS).

Texto completo con ranking (`tsvector`/`ts_rank`) + coincidencia difusa (trigram),
combinable con filtros estructurados: tipo, estado, rango de fecha, dependencia y
campos de metadato (`?meta.<campo>=`, contención JSONB sobre índice GIN). `q` es
opcional: sin texto, es una consulta filtrada ordenada por fecha.
"""

import uuid
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response
from orpycamcp_common.usuario import id_opcional, slug_de

from document.core.database import get_tenant_conn as get_db
from document.schemas.search import SearchResponse
from document.services.search_service import search_radicados

router = APIRouter(prefix="/api/v1/search", tags=["search"])


def _caller_id(request: Request) -> uuid.UUID | None:
    return id_opcional(request)


def _optional_canal(request: Request) -> str:
    """Canal de auditoría: lo decide `UsuarioActualMiddleware`, no una cabecera
    del cliente. Sin persona => 'system' (no se audita como consulta de alguien)."""
    from orpycamcp_common.usuario import canal_de

    return canal_de(request)


def _tenant_slug(request: Request) -> str:
    # get_tenant_conn (dependencia de la ruta) ya garantiza que X-Tenant-Slug
    # está presente y es un slug válido — lanza 400 antes de ejecutar el
    # handler. El default "" es defensa muerta: NO debe alcanzar `audit.append`
    # (donde `"" or SYSTEM_TENANT` desviaría el asiento a la cadena de SYSTEM).
    # Gemelo de `documents.py::_tenant_slug`.
    return slug_de(request)


@router.get("", response_model=SearchResponse)
async def search_documents(
    request: Request,
    response: Response,
    q: str | None = Query(None, min_length=2, description="Texto a buscar (opcional)"),
    doc_type: str | None = Query(None, description="E, S o I"),
    status: str | None = Query(None),
    date_from: date | None = Query(None, description="registered_at >= (ISO date)"),
    date_to: date | None = Query(None, description="registered_at <= (ISO date)"),
    dest_dept_code: int | None = Query(None),
    # Dónde está AHORA (ADR-029); `dest_dept_code` es a quién iba dirigido.
    ubicacion_dept_code: int | None = Query(None, ge=1, le=999999),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db),
    canal: str = Depends(_optional_canal),
) -> SearchResponse:
    meta = {
        k[len("meta.") :]: v
        for k, v in request.query_params.items()
        if k.startswith("meta.") and k != "meta."
    }
    total, hits = await search_radicados(
        conn,
        tenant_slug=_tenant_slug(request),
        caller_id=_caller_id(request),
        canal=canal,
        q=q,
        doc_type=doc_type,
        status=status,
        date_from=date_from,
        date_to=date_to,
        dest_dept_code=dest_dept_code,
        ubicacion_dept_code=ubicacion_dept_code,
        meta=meta,
        page=page,
        size=size,
    )
    response.headers["X-Total-Count"] = str(total)
    return SearchResponse(query=q, total=total, hits=hits, page=page, size=size)
