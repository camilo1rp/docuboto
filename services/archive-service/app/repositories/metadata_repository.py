# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de plantillas de metadatos del expediente (E03, ADR-007).

La plantilla se identifica por la serie TRD del expediente (`trd_serie_id`).
SQL crudo sobre `expediente_metadata_templates`; el caller fija el search_path
del tenant (igual que ArchiveRepository).
"""

from __future__ import annotations

import json
import uuid

import asyncpg

from app.core.database import tenant_schema

_COLS = "id, trd_serie_id, version, json_schema, activo, created_at"


class MetadataRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _set_search_path(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def get_active_template(self, trd_serie_id: uuid.UUID) -> asyncpg.Record | None:
        await self._set_search_path()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM expediente_metadata_templates "
            "WHERE trd_serie_id = $1 AND activo",
            trd_serie_id,
        )

    async def list(self, *, trd_serie_id: uuid.UUID | None) -> list[asyncpg.Record]:
        await self._set_search_path()
        if trd_serie_id is not None:
            return list(
                await self._conn.fetch(
                    f"SELECT {_COLS} FROM expediente_metadata_templates "
                    "WHERE trd_serie_id = $1 ORDER BY version",
                    trd_serie_id,
                )
            )
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM expediente_metadata_templates "
                "ORDER BY trd_serie_id, version"
            )
        )

    async def insert(
        self,
        *,
        trd_serie_id: uuid.UUID,
        version: int,
        json_schema: dict,
        activo: bool,
    ) -> asyncpg.Record:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO expediente_metadata_templates
                (trd_serie_id, version, json_schema, activo)
            VALUES ($1, $2, $3::jsonb, $4)
            RETURNING {_COLS}
            """,
            trd_serie_id,
            version,
            json.dumps(json_schema),
            activo,
        )
        assert row is not None
        return row
