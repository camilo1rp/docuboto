# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de vistos buenos secuenciales. search_path al tenant."""

from __future__ import annotations

import uuid

import asyncpg

from app.core.database import tenant_schema


class VistoBuenoRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _sp(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def exists(self, radicado_id: uuid.UUID) -> bool:
        await self._sp()
        val = await self._conn.fetchval(
            "SELECT 1 FROM visto_bueno WHERE radicado_id = $1 LIMIT 1", radicado_id
        )
        return val is not None

    async def crear_cadena(self, radicado_id: uuid.UUID, revisores: list[uuid.UUID]) -> None:
        await self._sp()
        async with self._conn.transaction():
            for orden, revisor in enumerate(revisores, start=1):
                await self._conn.execute(
                    "INSERT INTO visto_bueno (radicado_id, orden, revisor_id) "
                    "VALUES ($1, $2, $3)",
                    radicado_id,
                    orden,
                    revisor,
                )

    async def list(self, radicado_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                "SELECT id, orden, revisor_id, estado, comentario, decidido_at "
                "FROM visto_bueno WHERE radicado_id = $1 ORDER BY orden",
                radicado_id,
            )
        )

    async def active(self, radicado_id: uuid.UUID) -> asyncpg.Record | None:
        """Paso activo = menor orden aún pendiente (turno actual)."""
        await self._sp()
        return await self._conn.fetchrow(
            "SELECT id, orden, revisor_id, estado FROM visto_bueno "
            "WHERE radicado_id = $1 AND estado = 'pendiente' ORDER BY orden LIMIT 1",
            radicado_id,
        )

    async def is_reviewer(self, radicado_id: uuid.UUID, actor: uuid.UUID) -> bool:
        """Devuelve ``True`` si ``actor`` pertenece a la cadena de revisores del radicado."""
        await self._sp()
        val = await self._conn.fetchval(
            "SELECT 1 FROM visto_bueno WHERE radicado_id = $1 AND revisor_id = $2 LIMIT 1",
            radicado_id,
            actor,
        )
        return val is not None

    async def decide(self, vobo_id: uuid.UUID, estado: str, comentario: str | None) -> None:
        await self._sp()
        await self._conn.execute(
            "UPDATE visto_bueno SET estado=$2, comentario=$3, decidido_at=now() " "WHERE id=$1",
            vobo_id,
            estado,
            comentario,
        )
