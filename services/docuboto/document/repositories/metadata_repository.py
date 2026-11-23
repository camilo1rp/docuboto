# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de plantillas de metadatos (E03, ADR-007).

La conexión llega con search_path al schema del tenant (get_tenant_conn); aquí
solo se ejecuta SQL crudo sobre `metadata_templates`. El `json_schema` es un
JSON Schema (validado en la capa de servicio con jsonschema).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

_COLS = "id, tipo_documental, version, json_schema, activo, created_at"

_ELEM_COLS = (
    "id, clave, etiqueta, tipo_dato, longitud, ocurrencia_min, ocurrencia_max, "
    "modificable, valor_default, opciones, orden, searchable, mapeo_dublin_core, "
    "activo, created_at"
)
# Columnas JSONB que asyncpg devuelve como str y que insertamos como json.
_ELEM_JSON_COLS = frozenset({"valor_default", "opciones"})


class MetadataRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_active_template(self, tipo_documental: str) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM metadata_templates " "WHERE tipo_documental = $1 AND activo",
            tipo_documental,
        )

    async def get_by_id(self, template_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM metadata_templates WHERE id = $1", template_id
        )

    async def list(self, *, tipo_documental: str | None) -> list[asyncpg.Record]:
        if tipo_documental is not None:
            return list(
                await self._conn.fetch(
                    f"SELECT {_COLS} FROM metadata_templates "
                    "WHERE tipo_documental = $1 ORDER BY tipo_documental, version",
                    tipo_documental,
                )
            )
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM metadata_templates " "ORDER BY tipo_documental, version"
            )
        )

    async def insert(
        self,
        *,
        tipo_documental: str,
        version: int,
        json_schema: dict,
        activo: bool,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO metadata_templates (tipo_documental, version, json_schema, activo)
            VALUES ($1, $2, $3::jsonb, $4)
            RETURNING {_COLS}
            """,
            tipo_documental,
            version,
            json.dumps(json_schema),
            activo,
        )
        assert row is not None
        return row


class MetadataElementRepository:
    """Catálogo de elementos de metadato (E03, RF-MET-03)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def list(self, *, activo: bool | None) -> list[asyncpg.Record]:
        if activo is not None:
            return list(
                await self._conn.fetch(
                    f"SELECT {_ELEM_COLS} FROM metadata_elements "
                    "WHERE activo = $1 ORDER BY orden, clave",
                    activo,
                )
            )
        return list(
            await self._conn.fetch(
                f"SELECT {_ELEM_COLS} FROM metadata_elements ORDER BY orden, clave"
            )
        )

    async def get(self, element_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_ELEM_COLS} FROM metadata_elements WHERE id = $1", element_id
        )

    async def insert(self, data: dict[str, Any]) -> asyncpg.Record:
        cols = list(data.keys())
        placeholders = []
        values: list[Any] = []
        for i, col in enumerate(cols, start=1):
            if col in _ELEM_JSON_COLS:
                placeholders.append(f"${i}::jsonb")
                values.append(json.dumps(data[col]))
            else:
                placeholders.append(f"${i}")
                values.append(data[col])
        row = await self._conn.fetchrow(
            f"INSERT INTO metadata_elements ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING {_ELEM_COLS}",
            *values,
        )
        assert row is not None
        return row

    async def update(self, element_id: uuid.UUID, data: dict[str, Any]) -> asyncpg.Record | None:
        if not data:
            return await self.get(element_id)
        set_parts = []
        values: list[Any] = []
        idx = 1
        for col, val in data.items():
            if col in _ELEM_JSON_COLS:
                set_parts.append(f"{col} = ${idx}::jsonb")
                values.append(json.dumps(val))
            else:
                set_parts.append(f"{col} = ${idx}")
                values.append(val)
            idx += 1
        values.append(element_id)
        return await self._conn.fetchrow(
            f"UPDATE metadata_elements SET {', '.join(set_parts)} "
            f"WHERE id = ${idx} RETURNING {_ELEM_COLS}",
            *values,
        )

    async def delete(self, element_id: uuid.UUID) -> bool:
        result = await self._conn.execute("DELETE FROM metadata_elements WHERE id = $1", element_id)
        return result.endswith("1")
