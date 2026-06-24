# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de envíos postales (E20). search_path al tenant ya fijado por la conexión."""

from __future__ import annotations

import uuid

import asyncpg

_COLS = (
    "id, radicado_id, operador, guia, estado, destinatario, direccion, " "created_at, updated_at"
)


class PostalRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        radicado_id: uuid.UUID,
        operador: str,
        guia: str,
        destinatario: str | None,
        direccion: str | None,
    ) -> asyncpg.Record:
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                f"""
                INSERT INTO postal_shipment
                    (radicado_id, operador, guia, destinatario, direccion)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING {_COLS}
                """,
                radicado_id,
                operador,
                guia,
                destinatario,
                direccion,
            )
            assert row is not None
            await self._conn.execute(
                "INSERT INTO postal_shipment_event (shipment_id, estado, descripcion) "
                "VALUES ($1, 'registrado', 'Envío registrado')",
                row["id"],
            )
        return row

    async def get(self, shipment_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM postal_shipment WHERE id = $1", shipment_id
        )

    async def list_by_radicado(self, radicado_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM postal_shipment WHERE radicado_id = $1 "
                "ORDER BY created_at",
                radicado_id,
            )
        )

    async def events(self, shipment_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT estado, descripcion, ts FROM postal_shipment_event "
                "WHERE shipment_id = $1 ORDER BY ts ASC",
                shipment_id,
            )
        )

    async def update_estado(
        self, shipment_id: uuid.UUID, estado: str, descripcion: str | None
    ) -> asyncpg.Record | None:
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                f"UPDATE postal_shipment SET estado = $2, updated_at = now() "
                f"WHERE id = $1 RETURNING {_COLS}",
                shipment_id,
                estado,
            )
            if row is None:
                return None
            await self._conn.execute(
                "INSERT INTO postal_shipment_event (shipment_id, estado, descripcion) "
                "VALUES ($1, $2, $3)",
                shipment_id,
                estado,
                descripcion,
            )
        return row
