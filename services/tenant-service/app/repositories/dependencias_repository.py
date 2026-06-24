# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos de dependencias (organigrama) por tenant (E14).

SQL crudo; el search_path lo fija get_tenant_conn al schema del tenant.
La tabla es jerárquica (parent_id autorreferenciado). E01 la consulta por
`codigo` (depe_codi) para numerar radicados por dependencia.
"""

import uuid

import asyncpg

_COLS = "id, parent_id, codigo, sigla, nombre, depe_rad_tp2, activo, " "created_at, updated_at"


class DependenciaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def list(
        self, *, activo: bool | None = None, parent_id: uuid.UUID | None = None
    ) -> list[asyncpg.Record]:
        clauses: list[str] = []
        params: list[object] = []
        if activo is not None:
            params.append(activo)
            clauses.append(f"activo = ${len(params)}")
        if parent_id is not None:
            params.append(parent_id)
            clauses.append(f"parent_id = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return await self._conn.fetch(
            f"SELECT {_COLS} FROM dependencias {where} ORDER BY codigo", *params
        )

    async def get(self, dependencia_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM dependencias WHERE id = $1", dependencia_id
        )

    async def get_by_codigo(self, codigo: int) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM dependencias WHERE codigo = $1", codigo
        )

    async def insert(
        self,
        *,
        codigo: int,
        sigla: str,
        nombre: str,
        parent_id: uuid.UUID | None,
        depe_rad_tp2: bool,
        activo: bool,
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            f"""
            INSERT INTO dependencias
                (codigo, sigla, nombre, parent_id, depe_rad_tp2, activo)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {_COLS}
            """,
            codigo,
            sigla,
            nombre,
            parent_id,
            depe_rad_tp2,
            activo,
        )

    async def update(
        self,
        dependencia_id: uuid.UUID,
        *,
        fields: dict[str, object],
    ) -> asyncpg.Record | None:
        """Actualiza solo los campos presentes en ``fields``. Devuelve None si no existe."""
        if not fields:
            return await self.get(dependencia_id)
        sets: list[str] = []
        params: list[object] = []
        for col, val in fields.items():
            params.append(val)
            sets.append(f"{col} = ${len(params)}")
        params.append(dependencia_id)
        return await self._conn.fetchrow(
            f"""
            UPDATE dependencias SET {', '.join(sets)}, updated_at = now()
            WHERE id = ${len(params)}
            RETURNING {_COLS}
            """,
            *params,
        )

    async def has_children(self, dependencia_id: uuid.UUID) -> bool:
        return bool(
            await self._conn.fetchval(
                "SELECT 1 FROM dependencias WHERE parent_id = $1 LIMIT 1",
                dependencia_id,
            )
        )

    async def delete(self, dependencia_id: uuid.UUID) -> bool:
        result = await self._conn.execute("DELETE FROM dependencias WHERE id = $1", dependencia_id)
        return result.endswith("1")
