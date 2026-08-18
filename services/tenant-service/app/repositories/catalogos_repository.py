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

`causales` añade `ambito`: a qué acto pertenece cada causal (devolución, anulación,
reasignación). Es el único catálogo que lo necesita —los demás no describen actos—
y por eso la columna se declara aquí en vez de replicarse en los ocho.
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

# Catálogos cuya fila declara el acto al que pertenece (migración tenant 008).
CON_AMBITO: frozenset[str] = frozenset({"causales"})

_BASE = "id, codigo, nombre, activo"


def _columnas(tabla: str) -> str:
    """Lista de columnas a devolver, según la tabla tenga `ambito` o no."""
    return f"{_BASE}, ambito" if tabla in CON_AMBITO else _BASE


class CatalogoRepository:
    def __init__(self, conn: asyncpg.Connection, table: str) -> None:
        # table proviene siempre de CATALOGS.values() — seguro para interpolar.
        self._conn = conn
        self._t = table

    @property
    def soporta_ambito(self) -> bool:
        return self._t in CON_AMBITO

    async def list(
        self, *, activo: bool | None, ambito: str | None = None
    ) -> list[asyncpg.Record]:
        cols = _columnas(self._t)
        where: list[str] = []
        params: list[object] = []
        if activo is not None:
            params.append(activo)
            where.append(f"activo = ${len(params)}")
        # Filtrar por ámbito solo donde la columna existe: pedirlo sobre otro
        # catálogo es un error del llamante y lo corta el router con un 422, no
        # una consulta que devuelve todo como si el filtro se hubiera aplicado.
        if ambito is not None and self.soporta_ambito:
            params.append(ambito)
            where.append(f"ambito = ${len(params)}")
        sql = f"SELECT {cols} FROM {self._t}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return await self._conn.fetch(sql + " ORDER BY codigo", *params)

    async def insert(
        self, *, codigo: str, nombre: str, activo: bool, ambito: str | None = None
    ) -> asyncpg.Record:
        cols = _columnas(self._t)
        if self.soporta_ambito:
            return await self._conn.fetchrow(
                f"INSERT INTO {self._t} (codigo, nombre, activo, ambito) "
                f"VALUES ($1, $2, $3, COALESCE($4, 'general')) RETURNING {cols}",
                codigo,
                nombre,
                activo,
                ambito,
            )
        return await self._conn.fetchrow(
            f"INSERT INTO {self._t} (codigo, nombre, activo) VALUES ($1, $2, $3) "
            f"RETURNING {cols}",
            codigo,
            nombre,
            activo,
        )

    async def update(
        self, item_id: uuid.UUID, *, fields: dict[str, object]
    ) -> asyncpg.Record | None:
        cols = _columnas(self._t)
        if not fields:
            return await self._conn.fetchrow(
                f"SELECT {cols} FROM {self._t} WHERE id = $1", item_id
            )
        sets: list[str] = []
        params: list[object] = []
        for col, val in fields.items():
            params.append(val)
            sets.append(f"{col} = ${len(params)}")
        params.append(item_id)
        return await self._conn.fetchrow(
            f"UPDATE {self._t} SET {', '.join(sets)} WHERE id = ${len(params)} "
            f"RETURNING {cols}",
            *params,
        )

    async def delete(self, item_id: uuid.UUID) -> bool:
        result = await self._conn.execute(f"DELETE FROM {self._t} WHERE id = $1", item_id)
        return result.endswith("1")
