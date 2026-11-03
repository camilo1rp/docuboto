# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Cuadro de clasificación de los radicados clasificados (C12, ADR-039).

Resuelve, para un conjunto de radicados, en qué serie/subserie de la TRD están: la
unión `expediente_radicados` (vinculados) -> `expedientes.trd_serie_id` ->
`trd_series` (+ su serie madre y la dependencia productora). SQL crudo; el
`search_path` ya viene fijado por el llamante.

NO selecciona ningún dato del radicado (esa tabla vive en document-service): solo su
identificador como llave de unión, y no sale de la capa de servicio.
"""

from __future__ import annotations

import uuid

import asyncpg

#: Productoras de una serie, "A; B", en orden estable. Se busca en la serie madre si la
#: fila es una subserie, y en la propia si la madre no las declara.
_PRODUCTORAS = """
    COALESCE(
        (SELECT string_agg(d.dependencia_nombre, '; ' ORDER BY d.dependencia_nombre)
           FROM trd_serie_dependencia_productora d WHERE d.trd_serie_id = COALESCE(p.id, s.id)),
        (SELECT string_agg(d.dependencia_nombre, '; ' ORDER BY d.dependencia_nombre)
           FROM trd_serie_dependencia_productora d WHERE d.trd_serie_id = s.id)
    )
"""


async def series_de_radicados(
    conn: asyncpg.Connection, radicado_ids: list[uuid.UUID]
) -> list[asyncpg.Record]:
    """Una fila por (radicado, serie): un radicado en N expedientes con serie sale N
    veces (semántica de conjunto). Los vínculos `excluido` no cuentan. Un expediente sin
    `trd_serie_id` no aporta fila: el radicado queda «sin serie» si no tiene otra."""
    if not radicado_ids:
        return []
    return list(
        await conn.fetch(
            f"""
            SELECT DISTINCT er.radicado_id,
                   COALESCE(p.code, s.code) AS serie_codigo,
                   COALESCE(p.name, s.name) AS serie_nombre,
                   CASE WHEN p.id IS NULL THEN NULL ELSE s.code END AS subserie_codigo,
                   CASE WHEN p.id IS NULL THEN NULL ELSE s.name END AS subserie_nombre,
                   {_PRODUCTORAS} AS dependencia_responsable
              FROM expediente_radicados er
              JOIN expedientes e ON e.id = er.expediente_id
              JOIN trd_series s ON s.id = e.trd_serie_id
              LEFT JOIN trd_series p ON p.id = s.parent_id
             WHERE er.estado = 'vinculado' AND er.radicado_id = ANY($1::uuid[])
            """,
            radicado_ids,
        )
    )
