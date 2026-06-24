# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Agregaciones para reportes/estadísticas de radicados (E09, F2, ADR-014).

Todo se resuelve con SQL nativo (`GROUP BY`, `COUNT`, `date_trunc`) sobre el schema
del tenant. La conexión llega con search_path al tenant.
"""

from __future__ import annotations

from datetime import date

import asyncpg


class ReportRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def _date_filter(
        self, date_from: date | None, date_to: date | None
    ) -> tuple[str, list[object]]:
        conds: list[str] = []
        params: list[object] = []
        if date_from is not None:
            params.append(date_from)
            conds.append(f"registered_at >= ${len(params)}")
        if date_to is not None:
            params.append(date_to)
            conds.append(f"registered_at <= ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return where, params

    async def radicado_summary(
        self, date_from: date | None, date_to: date | None
    ) -> tuple[
        list[asyncpg.Record],
        list[asyncpg.Record],
        list[asyncpg.Record],
        list[asyncpg.Record],
    ]:
        where, params = self._date_filter(date_from, date_to)
        by_type = await self._conn.fetch(
            f"SELECT doc_type, COUNT(*) AS n FROM radicados {where} GROUP BY doc_type",
            *params,
        )
        by_status = await self._conn.fetch(
            f"SELECT status, COUNT(*) AS n FROM radicados {where} GROUP BY status",
            *params,
        )
        by_month = await self._conn.fetch(
            f"SELECT to_char(date_trunc('month', registered_at), 'YYYY-MM') AS month, "
            f"COUNT(*) AS n FROM radicados {where} GROUP BY 1 ORDER BY 1",
            *params,
        )
        by_dependencia = await self._conn.fetch(
            f"SELECT COALESCE(dest_dept, '(sin asignar)') AS dep, COUNT(*) AS n "
            f"FROM radicados {where} GROUP BY 1 ORDER BY n DESC",
            *params,
        )
        return by_type, by_status, by_month, by_dependencia
