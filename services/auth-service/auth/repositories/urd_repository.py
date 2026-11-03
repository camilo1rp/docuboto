# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio URD (Usuario-Rol-Dependencia) y contexto activo (E08).

Una URD por (usuario, dependencia) [RN-4]; una sola principal por usuario. El
contexto activo se guarda en `auth_users.active_depe_id` (ADR-013: el cambio de
contexto no reemite token). La conexión llega con search_path al tenant.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

_COLS = "id, user_id, depe_id, rol_id, es_principal, is_active, created_at"


class URDRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def list_by_user(self, user_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM urd_usuaroldep "
                "WHERE user_id = $1 ORDER BY es_principal DESC, depe_id",
                user_id,
            )
        )

    async def get(self, user_id: uuid.UUID, depe_id: int) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM urd_usuaroldep WHERE user_id = $1 AND depe_id = $2",
            user_id,
            depe_id,
        )

    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        depe_id: int,
        rol_id: uuid.UUID,
        es_principal: bool,
    ) -> asyncpg.Record:
        async with self._conn.transaction():
            if es_principal:
                # Solo una principal por usuario (ux_urd_principal).
                await self._conn.execute(
                    "UPDATE urd_usuaroldep SET es_principal = false WHERE user_id = $1",
                    user_id,
                )
            row = await self._conn.fetchrow(
                f"""
                INSERT INTO urd_usuaroldep (user_id, depe_id, rol_id, es_principal)
                VALUES ($1, $2, $3, $4)
                RETURNING {_COLS}
                """,
                user_id,
                depe_id,
                rol_id,
                es_principal,
            )
        assert row is not None
        return row

    async def delete(self, user_id: uuid.UUID, urd_id: uuid.UUID) -> bool:
        result = await self._conn.execute(
            "DELETE FROM urd_usuaroldep WHERE id = $1 AND user_id = $2",
            urd_id,
            user_id,
        )
        return result.endswith("1")

    async def set_active_depe(self, user_id: uuid.UUID, depe_id: int) -> None:
        await self._conn.execute(
            "UPDATE auth_users SET active_depe_id = $2 WHERE id = $1",
            user_id,
            depe_id,
        )

    async def get_active_depe(self, user_id: uuid.UUID) -> int | None:
        return await self._conn.fetchval(
            "SELECT active_depe_id FROM auth_users WHERE id = $1", user_id
        )

    async def log_historico(
        self,
        *,
        executor_id: uuid.UUID | None,
        target_id: uuid.UUID,
        accion: str,
        permisos: dict[str, Any] | None = None,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO usua_historico (executor_id, target_id, accion, permisos_json)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            executor_id,
            target_id,
            accion,
            json.dumps(permisos or {}),
        )
