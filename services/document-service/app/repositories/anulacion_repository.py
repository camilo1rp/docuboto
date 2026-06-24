# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de anulación de radicados (2 pasos). search_path fijado por la conexión."""

from __future__ import annotations

import uuid

import asyncpg

_COLS = (
    "id, radicado_id, estado, causal, motivo, solicitado_por, solicitado_at, "
    "decidido_por, decidido_at, observacion"
)


class AnulacionRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_active(self, radicado_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM radicado_anulacion "
            "WHERE radicado_id = $1 AND estado = 'solicitada'",
            radicado_id,
        )

    async def get_latest(self, radicado_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM radicado_anulacion WHERE radicado_id = $1 "
            "ORDER BY solicitado_at DESC LIMIT 1",
            radicado_id,
        )

    async def radicado_status(self, radicado_id: uuid.UUID) -> str | None:
        return await self._conn.fetchval("SELECT status FROM radicados WHERE id = $1", radicado_id)

    async def insert_solicitud(
        self,
        *,
        radicado_id: uuid.UUID,
        causal: str,
        motivo: str | None,
        solicitado_por: uuid.UUID | None,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"INSERT INTO radicado_anulacion (radicado_id, causal, motivo, solicitado_por) "
            f"VALUES ($1, $2, $3, $4) RETURNING {_COLS}",
            radicado_id,
            causal,
            motivo,
            solicitado_por,
        )
        assert row is not None
        return row

    async def decide(
        self,
        *,
        anulacion_id: uuid.UUID,
        radicado_id: uuid.UUID,
        aprobar: bool,
        decidido_por: uuid.UUID | None,
        observacion: str | None,
    ) -> asyncpg.Record:
        """Aprueba/rechaza atómicamente; al aprobar marca el radicado como anulado."""
        estado = "aprobada" if aprobar else "rechazada"
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                f"UPDATE radicado_anulacion SET estado=$2, decidido_por=$3, "
                f"decidido_at=now(), observacion=$4 WHERE id=$1 RETURNING {_COLS}",
                anulacion_id,
                estado,
                decidido_por,
                observacion,
            )
            if aprobar:
                await self._conn.execute(
                    "UPDATE radicados SET status='anulado' WHERE id=$1", radicado_id
                )
        assert row is not None
        return row
