# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reportes/estadísticas de radicados (E09, F2) + export CSV (AGN)

y el índice de información clasificada y reservada (E08, Ley 1712/2014 art. 20)."""

import csv
import io
import uuid
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.core.clearance import require_permission
from app.core.database import get_tenant_conn as get_db
from app.repositories.report_repository import ReportRepository
from app.schemas.report import MonthCount, ReportSummary
from app.services.document_service import DocumentService

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"


def _caller_id(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _build_summary(
    conn: asyncpg.Connection, date_from: date | None, date_to: date | None
) -> ReportSummary:
    by_type, by_status, by_month, by_dep = await ReportRepository(conn).radicado_summary(
        date_from, date_to
    )
    by_doc_type = {r["doc_type"]: r["n"] for r in by_type}
    return ReportSummary(
        total=sum(by_doc_type.values()),
        by_doc_type=by_doc_type,
        by_status={r["status"]: r["n"] for r in by_status},
        by_month=[MonthCount(month=r["month"], count=r["n"]) for r in by_month],
        by_dependencia={r["dep"]: r["n"] for r in by_dep},
    )


@router.get("/radicados", response_model=ReportSummary)
async def radicado_report(
    date_from: date | None = Query(None, description="registered_at >= (ISO date)"),
    date_to: date | None = Query(None, description="registered_at <= (ISO date)"),
    conn: asyncpg.Connection = Depends(get_db),
) -> ReportSummary:
    """Resumen estadístico: conteos por tipo, estado, mes y dependencia."""
    return await _build_summary(conn, date_from, date_to)


@router.get("/radicados.csv", summary="Export del resumen estadístico en CSV (AGN)")
async def radicado_report_csv(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    summary = await _build_summary(conn, date_from, date_to)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["seccion", "clave", "valor"])
    writer.writerow(["total", "", summary.total])
    for k, v in summary.by_doc_type.items():
        writer.writerow(["tipo", k, v])
    for k, v in summary.by_status.items():
        writer.writerow(["estado", k, v])
    for m in summary.by_month:
        writer.writerow(["mes", m.month, m.count])
    for k, v in summary.by_dependencia.items():
        writer.writerow(["dependencia", k, v])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=radicados.csv"},
    )


# ---------------------------------------------------------------------------
# Índice de información clasificada y reservada (E08, Ley 1712/2014 art. 20 +
# Decreto 1081/2015). Registro CONTENT-FREE (no expone el asunto/contenido) y
# COMPLETO (todos los radicados nivel ≥ 2): es una PUBLICACIÓN de metadata, no
# una lectura de contenido, así que es ORTOGONAL al clearance de lectura (no
# aplica no-read-up — el registro debe ser íntegro para cumplir su función
# legal). Se gatea por `PERM_RECLASIFICAR` (el oficial que clasifica es quien
# genera/publica el registro de transparencia).
# ---------------------------------------------------------------------------
def _csv_safe(value: object) -> object:
    """Neutraliza la inyección de fórmulas CSV (Excel/Sheets): un valor de texto
    que empieza por `= + - @` o un control (TAB/CR) se interpreta como fórmula al
    abrir el archivo. Se antepone un apóstrofo para forzar su lectura como texto.
    `fundamento`/`causal` son texto libre capturado en la reclasificación."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


_INDICE_CSV_COLS = [
    "numero_radicado",
    "tipo_radicado",
    "clasificacion",
    "excepcion_ley_1712",
    "fundamento_juridico",
    "causal_reserva",
    "plazo_reserva_meses",
    "fecha_clasificacion",
    "fecha_radicacion",
]


@router.get(
    "/indice-reservado",
    summary="Índice de información clasificada y reservada (Ley 1712 art. 20)",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR"))],
)
async def indice_reservado(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
):
    slug = request.headers.get(_TENANT_HEADER, "")
    service = DocumentService(conn, slug)
    return await service.indice_clasificados(_caller_id(request))


@router.get(
    "/indice-reservado.csv",
    summary="Índice de información clasificada y reservada en CSV (Ley 1712 art. 20)",
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR"))],
)
async def indice_reservado_csv(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    slug = request.headers.get(_TENANT_HEADER, "")
    service = DocumentService(conn, slug)
    indice = await service.indice_clasificados(_caller_id(request))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_INDICE_CSV_COLS)
    for it in indice.items:
        writer.writerow(
            [
                _csv_safe(it.numero_radicado),
                it.tipo_radicado,
                it.clasificacion,
                it.excepcion_ley_1712,
                _csv_safe(it.fundamento_juridico or ""),
                _csv_safe(it.causal_reserva or ""),
                "" if it.plazo_reserva_meses is None else it.plazo_reserva_meses,
                it.fecha_clasificacion.isoformat() if it.fecha_clasificacion else "",
                it.fecha_radicacion.isoformat(),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=indice-reservado.csv"},
    )
