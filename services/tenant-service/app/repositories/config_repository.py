# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos de configuración por tenant (E14). SQL crudo; search_path
fijado al schema del tenant por get_tenant_conn."""

import json
import uuid
from datetime import date
from typing import Any

import asyncpg


class ConfigRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # --- festivos / días no hábiles ----------------------------------------
    async def list_holidays(self, year: int | None) -> list[asyncpg.Record]:
        if year is not None:
            return await self._conn.fetch(
                "SELECT id, fecha, descripcion, tipo, is_national FROM non_business_days "
                "WHERE EXTRACT(YEAR FROM fecha) = $1 ORDER BY fecha",
                year,
            )
        return await self._conn.fetch(
            "SELECT id, fecha, descripcion, tipo, is_national FROM non_business_days "
            "ORDER BY fecha"
        )

    async def insert_holiday(
        self, *, fecha: date, descripcion: str, tipo: str, is_national: bool
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            """
            INSERT INTO non_business_days (fecha, descripcion, tipo, is_national)
            VALUES ($1, $2, $3, $4)
            RETURNING id, fecha, descripcion, tipo, is_national
            """,
            fecha,
            descripcion,
            tipo,
            is_national,
        )

    async def delete_holiday(self, holiday_id: uuid.UUID) -> bool:
        result = await self._conn.execute("DELETE FROM non_business_days WHERE id = $1", holiday_id)
        return result.endswith("1")

    async def bulk_insert_holidays(self, rows: list[tuple[date, str, str, bool]]) -> int:
        """Inserta festivos ignorando los ya presentes (ON CONFLICT). Devuelve creados."""
        created = 0
        for fecha, descripcion, tipo, is_national in rows:
            tag = await self._conn.execute(
                """
                INSERT INTO non_business_days (fecha, descripcion, tipo, is_national)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (fecha) DO NOTHING
                """,
                fecha,
                descripcion,
                tipo,
                is_national,
            )
            if tag.endswith("1"):
                created += 1
        return created

    async def non_business_dates(self, start: date, end: date) -> set[date]:
        rows = await self._conn.fetch(
            "SELECT fecha FROM non_business_days WHERE fecha BETWEEN $1 AND $2",
            start,
            end,
        )
        return {r["fecha"] for r in rows}

    # --- parámetros ---------------------------------------------------------
    async def get_params(self) -> dict[str, Any]:
        rows = await self._conn.fetch("SELECT key, value FROM sgd_param_admin")
        return {r["key"]: json.loads(r["value"]) for r in rows}

    async def get_param(self, key: str) -> Any | None:
        val = await self._conn.fetchval("SELECT value FROM sgd_param_admin WHERE key = $1", key)
        return json.loads(val) if val is not None else None

    async def upsert_param(self, key: str, value: Any, actor_id: uuid.UUID | None) -> None:
        """Inserta/actualiza el parámetro y audita el cambio en la misma transacción."""
        async with self._conn.transaction():
            old = await self._conn.fetchval("SELECT value FROM sgd_param_admin WHERE key = $1", key)
            await self._conn.execute(
                """
                INSERT INTO sgd_param_admin (key, value, updated_by)
                VALUES ($1, $2::jsonb, $3)
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                """,
                key,
                json.dumps(value),
                actor_id,
            )
            await self._conn.execute(
                """
                INSERT INTO admin_audit (entity, entity_id, action, old_value, new_value, actor_id)
                VALUES ('param', $1, $2, $3::jsonb, $4::jsonb, $5)
                """,
                key,
                "update" if old is not None else "create",
                old,
                json.dumps(value),
                actor_id,
            )
