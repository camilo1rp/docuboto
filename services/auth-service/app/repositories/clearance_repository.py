# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Clasificación de seguridad y clearance por rol (E08, RF-SEG-08).

Catálogo `security_levels` (1=Pública, 2=Reservada, 3=Clasificada) y matriz
`role_clearance` (grupo → nivel máximo accesible). El nivel concreto del documento
lo guarda document-service; aquí se resuelve el clearance del llamante (mínimo
privilegio). La conexión llega con search_path al tenant.
"""

from __future__ import annotations

import uuid

import asyncpg


class ClearanceRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def list_levels(self) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch("SELECT code, nombre FROM security_levels ORDER BY code")
        )

    async def level_exists(self, code: int) -> bool:
        val = await self._conn.fetchval("SELECT 1 FROM security_levels WHERE code = $1", code)
        return val is not None

    async def set_group_clearance(self, group_id: uuid.UUID, max_level: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO role_clearance (group_id, max_level)
            VALUES ($1, $2)
            ON CONFLICT (group_id) DO UPDATE SET max_level = EXCLUDED.max_level
            """,
            group_id,
            max_level,
        )

    async def caller_max_level(self, user_id: uuid.UUID) -> int:
        """Nivel máximo accesible del usuario = MAX sobre sus grupos. Sin clearance
        asignado, solo accede a PUBLICA (1)."""
        val = await self._conn.fetchval(
            """
            SELECT MAX(rc.max_level)
            FROM auth_memberships m
            JOIN role_clearance rc ON rc.group_id = m.group_id
            WHERE m.user_id = $1
            """,
            user_id,
        )
        return int(val) if val is not None else 1
