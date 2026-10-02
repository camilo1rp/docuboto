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

from app.core.clearance import resolve_caller_clearance
from app.core.database import tenant_schema


class VistoBuenoRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _sp(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def caller_clearance(self, user_id: uuid.UUID | None) -> int:
        """Clearance del llamante (fail-closed a PUBLICA sin identidad)."""
        await self._sp()
        return await resolve_caller_clearance(self._conn, user_id)

    async def radicado_visible(self, radicado_id: uuid.UUID, max_clearance: int) -> bool:
        """El radicado existe Y el llamante puede leerlo (no-read-up en el WHERE)."""
        await self._sp()
        val = await self._conn.fetchval(
            "SELECT 1 FROM radicados WHERE id = $1 AND nivel_seguridad <= $2",
            radicado_id,
            max_clearance,
        )
        return val is not None

    async def revisores_sin_habilitacion(
        self, radicado_id: uuid.UUID, revisores: list[uuid.UUID]
    ) -> int:
        """Cuántos revisores NO alcanzan el nivel del radicado (misma regla que
        ``resolve_caller_clearance``: root=3, MAX(role_clearance), sin dato=PUBLICA)."""
        await self._sp()
        val = await self._conn.fetchval(
            "SELECT count(*) FROM unnest($2::uuid[]) AS t(rid) "
            "LEFT JOIN auth_users u ON u.id = t.rid "
            "WHERE COALESCE(CASE WHEN u.is_root THEN 3 ELSE ("
            "SELECT MAX(rc.max_level) FROM auth_memberships m "
            "JOIN role_clearance rc ON rc.group_id = m.group_id "
            "WHERE m.user_id = u.id) END, 1) "
            "< (SELECT nivel_seguridad FROM radicados WHERE id = $1)",
            radicado_id,
            revisores,
        )
        return int(val or 0)

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

    async def list(self, radicado_id: uuid.UUID, max_clearance: int) -> list[asyncpg.Record]:
        """Cadena del radicado; el no-read-up va en el JOIN (RF-SEG-08): un radicado
        por encima del clearance —o inexistente— no devuelve filas."""
        await self._sp()
        return list(
            await self._conn.fetch(
                "SELECT vb.id, vb.orden, vb.revisor_id, vb.estado, vb.comentario, "
                "vb.decidido_at FROM visto_bueno vb "
                "JOIN radicados r ON r.id = vb.radicado_id AND r.nivel_seguridad <= $2 "
                "WHERE vb.radicado_id = $1 ORDER BY vb.orden",
                radicado_id,
                max_clearance,
            )
        )

    async def active(
        self, radicado_id: uuid.UUID, max_clearance: int
    ) -> asyncpg.Record | None:
        """Paso activo = menor orden aún pendiente (turno actual), solo si el
        revisor puede leer el radicado: ser designado no habilita aprobar lo que
        no se puede leer."""
        await self._sp()
        return await self._conn.fetchrow(
            "SELECT vb.id, vb.orden, vb.revisor_id, vb.estado FROM visto_bueno vb "
            "JOIN radicados r ON r.id = vb.radicado_id AND r.nivel_seguridad <= $2 "
            "WHERE vb.radicado_id = $1 AND vb.estado = 'pendiente' "
            "ORDER BY vb.orden LIMIT 1",
            radicado_id,
            max_clearance,
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
