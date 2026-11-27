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
        # Dependencia de RADICACIÓN (a quién iba dirigido), no dónde está ahora.
        by_dependencia = await self._conn.fetch(
            # Se agrupa por CÓDIGO (ADR-032): dos dependencias homónimas no se funden y un
            # renombre no parte la serie. La etiqueta es el nombre ACTUAL del catálogo; el grupo
            # sin código lleva una etiqueta FIJA (MIN(nombre) rotularía con un nombre que
            # no es el de todas las filas del grupo).
            f"SELECT dest_dept_code AS codigo, COALESCE("
            f"(SELECT d.nombre FROM dependencias d WHERE d.codigo = radicados.dest_dept_code), "
            f"CASE WHEN dest_dept_code IS NULL THEN '(sin código)' ELSE MIN(dest_dept) END, "
            f"'(sin código)') AS dep, COUNT(*) AS n "
            f"FROM radicados {where} GROUP BY dest_dept_code ORDER BY n DESC",
            *params,
        )
        # Ubicación ACTUAL (ADR-029) de lo que aún está en trámite: los terminales
        # (cerrado/anulado) ya no están «en» ninguna dependencia a efectos de carga
        # de trabajo. La anterior agrupa por la dependencia de RADICACIÓN.
        no_terminal = "status NOT IN ('closed', 'anulado')"
        donde_ubicacion = f"{where} AND {no_terminal}" if where else f"WHERE {no_terminal}"
        by_ubicacion = await self._conn.fetch(
            f"SELECT ubicacion_dept_code AS codigo, COALESCE("
            f"(SELECT d.nombre FROM dependencias d WHERE d.codigo = radicados.ubicacion_dept_code), "
            f"CASE WHEN ubicacion_dept_code IS NULL THEN '(sin código)' ELSE MIN(ubicacion_dept) END, "
            f"'(sin código)') AS dep, COUNT(*) AS n "
            f"FROM radicados {donde_ubicacion} GROUP BY ubicacion_dept_code ORDER BY n DESC",
            *params,
        )
        return by_type, by_status, by_month, by_dependencia, by_ubicacion
