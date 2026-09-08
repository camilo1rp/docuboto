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

import json
import uuid
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.core.clearance import resolve_caller_clearance
from app.core.database import get_tenant_conn as get_db
from app.schemas.search import SearchHit, SearchResponse
from app.services.document_service import audit_search_access

router = APIRouter(prefix="/api/v1/search", tags=["search"])

_USER_HEADER = "X-User-Id"
_TENANT_HEADER = "X-Tenant-Slug"

_SELECT = (
    "id, tracking_number, doc_type, subject, sender_name, sender_entity, doc_class, "
    "dest_dept, status, registered_at"
)


def _caller_id(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Canal de origen (X-Canal, inyectado server-side por el api-gateway,
    D-03; un cliente no puede forjarlo — ver `documents.py::_optional_canal`).
    Ausente => 'system' (no se audita como consulta de un usuario real)."""
    return x_canal or "system"


def _tenant_slug(request: Request) -> str:
    # get_tenant_conn (dependencia de la ruta) ya garantiza que X-Tenant-Slug
    # está presente y es un slug válido — lanza 400 antes de ejecutar el
    # handler. El default "" es defensa muerta: NO debe alcanzar `audit.append`
    # (donde `"" or SYSTEM_TENANT` desviaría el asiento a la cadena de SYSTEM).
    # Gemelo de `documents.py::_tenant_slug`.
    return request.headers.get(_TENANT_HEADER, "")


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
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db),
    canal: str = Depends(_optional_canal),
) -> SearchResponse:
    conds: list[str] = []
    params: list[object] = []
    idx = 1
    q_idx: int | None = None

    # Control de acceso por clasificación (RF-SEG-08 / RF-BUS-04): el llamante solo
    # ve radicados cuyo nivel de seguridad no supere su clearance (no read-up). El
    # aislamiento entre tenants ya lo da el search_path; esto restringe DENTRO del
    # tenant. Fail-closed a PUBLICA si no hay identidad. Siempre se aplica.
    clearance = await resolve_caller_clearance(conn, _caller_id(request))
    params.append(clearance)
    conds.append(f"nivel_seguridad <= ${idx}")
    idx += 1

    if q:
        params.append(q)
        q_idx = idx
        idx += 1
        # La configuración de texto va CUALIFICADA y es LA MISMA que construye el
        # `search_vector` (tenant/020_search_unaccent.sql). Consulta e índice
        # tienen que hablar el mismo idioma: con `'spanish'` a secas, buscar
        # «certificacion» devolvía CERO radicados mientras «certificación»
        # devolvía tres, y la pantalla decía «Sin resultados» — indistinguible
        # de «no existe». Ver ADR-014.
        conds.append(
            f"(search_vector @@ websearch_to_tsquery('public.spanish_unaccent'::regconfig, ${q_idx}) "
            f"OR tracking_number ILIKE '%' || ${q_idx} || '%' "
            f"OR sender_name ILIKE '%' || ${q_idx} || '%')"
        )
    if doc_type:
        params.append(doc_type)
        conds.append(f"doc_type = ${idx}")
        idx += 1
    if status:
        params.append(status)
        conds.append(f"status = ${idx}")
        idx += 1
    if date_from:
        params.append(date_from)
        conds.append(f"registered_at >= ${idx}")
        idx += 1
    if date_to:
        params.append(date_to)
        conds.append(f"registered_at <= ${idx}")
        idx += 1
    if dest_dept_code is not None:
        params.append(dest_dept_code)
        conds.append(f"dest_dept_code = ${idx}")
        idx += 1
    meta = {
        k[len("meta.") :]: v
        for k, v in request.query_params.items()
        if k.startswith("meta.") and k != "meta."
    }
    if meta:
        params.append(json.dumps(meta))
        conds.append(f"metadata @> ${idx}::jsonb")
        idx += 1

    where = " AND ".join(conds) if conds else "TRUE"
    rank_expr = (
        f"ts_rank(search_vector, websearch_to_tsquery('public.spanish_unaccent'::regconfig, ${q_idx}))" if q_idx else "0"
    )
    order_by = "rank DESC, registered_at DESC" if q_idx else "registered_at DESC"

    total: int = await conn.fetchval(f"SELECT COUNT(*) FROM radicados WHERE {where}", *params) or 0

    offset = (page - 1) * size
    limit_idx, offset_idx = idx, idx + 1
    rows = await conn.fetch(
        f"SELECT {_SELECT}, {rank_expr} AS rank FROM radicados "
        f"WHERE {where} ORDER BY {order_by} "
        f"LIMIT ${limit_idx} OFFSET ${offset_idx}",
        *params,
        size,
        offset,
    )

    hits = [
        SearchHit(
            id=r["id"],
            tracking_number=r["tracking_number"],
            doc_type=r["doc_type"],
            subject=r["subject"],
            sender_name=r["sender_name"],
            sender_entity=r["sender_entity"],
            doc_class=r["doc_class"],
            dest_dept=r["dest_dept"],
            status=r["status"],
            registered_at=r["registered_at"],
            rank=float(r["rank"]),
        )
        for r in rows
    ]
    response.headers["X-Total-Count"] = str(total)

    # Traza AGREGADA de búsqueda (RF-BUS-10/E09; Ley 1712/2014, Decreto
    # 1081/2015): UNA sola entrada por consulta exitosa, nunca por hit. `q`
    # crudo NUNCA se registra (podría ser PII) — solo su presencia/longitud;
    # de `meta.*` solo las CLAVES consultadas. Ver `audit_search_access` en
    # `app.services.document_service` (gate + best-effort centralizados ahí;
    # este router no pasa por `DocumentService` porque ejecuta SQL crudo
    # directo, a diferencia de `list_documents`).
    await audit_search_access(
        conn,
        tenant_slug=_tenant_slug(request),
        filtros={
            "q_presente": q is not None,
            "q_len": len(q) if q else None,
            "doc_type": doc_type,
            "status": status,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "dest_dept_code": dest_dept_code,
            "metadata_filter_keys": sorted(meta) if meta else None,
        },
        total=total,
        page=page,
        size=size,
        user_id=_caller_id(request),
        canal=canal,
    )
    return SearchResponse(query=q, total=total, hits=hits, page=page, size=size)
