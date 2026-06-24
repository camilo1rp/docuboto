# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos de catálogos de referencia por tenant (E14).

Todos los catálogos comparten la forma (id, codigo, nombre, activo). El nombre de
tabla se valida contra una whitelist antes de interpolarse en el SQL.
"""

import uuid

import asyncpg

# Whitelist: única fuente de nombres de tabla interpolables. Nunca aceptar otros.
CATALOGS: dict[str, str] = {
    "tipos-identificacion": "tipos_identificacion",
    "tipos-remitente": "tipos_remitente",
    "medios-recepcion": "medios_recepcion",
    "tipos-anexo": "tipos_anexo",
    "causales": "causales",
    "formas-envio": "formas_envio",
    "soportes": "soportes",
    "mensajes-rapidos": "mensajes_rapidos",
}


class CatalogoRepository:
    def __init__(self, conn: asyncpg.Connection, table: str) -> None:
        # table proviene siempre de CATALOGS.values() — seguro para interpolar.
        self._conn = conn
        self._t = table

    async def list(self, *, activo: bool | None) -> list[asyncpg.Record]:
        if activo is None:
            return await self._conn.fetch(
                f"SELECT id, codigo, nombre, activo FROM {self._t} ORDER BY codigo"
            )
        return await self._conn.fetch(
            f"SELECT id, codigo, nombre, activo FROM {self._t} "
            "WHERE activo = $1 ORDER BY codigo",
            activo,
        )

    async def insert(self, *, codigo: str, nombre: str, activo: bool) -> asyncpg.Record:
        return await self._conn.fetchrow(
            f"INSERT INTO {self._t} (codigo, nombre, activo) VALUES ($1, $2, $3) "
            "RETURNING id, codigo, nombre, activo",
            codigo,
            nombre,
            activo,
        )

    async def update(
        self, item_id: uuid.UUID, *, fields: dict[str, object]
    ) -> asyncpg.Record | None:
        if not fields:
            return await self._conn.fetchrow(
                f"SELECT id, codigo, nombre, activo FROM {self._t} WHERE id = $1", item_id
            )
        sets: list[str] = []
        params: list[object] = []
        for col, val in fields.items():
            params.append(val)
            sets.append(f"{col} = ${len(params)}")
        params.append(item_id)
        return await self._conn.fetchrow(
            f"UPDATE {self._t} SET {', '.join(sets)} WHERE id = ${len(params)} "
            "RETURNING id, codigo, nombre, activo",
            *params,
        )

    async def delete(self, item_id: uuid.UUID) -> bool:
        result = await self._conn.execute(f"DELETE FROM {self._t} WHERE id = $1", item_id)
        return result.endswith("1")
