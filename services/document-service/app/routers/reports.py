# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reportes/estadísticas de radicados (E09, F2) + export CSV (AGN)."""

import csv
import io
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.database import get_tenant_conn as get_db
from app.repositories.report_repository import ReportRepository
from app.schemas.report import MonthCount, ReportSummary

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


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
