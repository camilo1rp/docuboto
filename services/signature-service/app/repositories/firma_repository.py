# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de firmas (signature-service). search_path fijado por la conexión."""

from __future__ import annotations

import uuid

import asyncpg

_COLS = (
    "id, objeto_tipo, objeto_id, formato_solicitado, firmante_id, "
    "payload_sha256, firma_blob, provider, created_at"
)


class FirmaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(self, data: dict) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""INSERT INTO firma (objeto_tipo, objeto_id, formato_solicitado,
                firmante_id, payload_sha256, firma_blob, provider)
                VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING {_COLS}""",
            data["objeto_tipo"],
            data.get("objeto_id"),
            data.get("formato_solicitado"),
            data.get("firmante_id"),
            data["payload_sha256"],
            data["firma_blob"],
            data["provider"],
        )
        assert row is not None
        return row

    async def get(self, firma_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(f"SELECT {_COLS} FROM firma WHERE id = $1", firma_id)
