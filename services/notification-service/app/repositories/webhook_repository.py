# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de suscripciones de webhooks (E11). search_path ya fijado por la capa de conexión."""

from __future__ import annotations

import uuid

import asyncpg


class WebhookRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self, *, url: str, event_types: list[str], secret: str | None
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            "INSERT INTO webhook_subscription (url, event_types, secret) "
            "VALUES ($1, $2, $3) "
            "RETURNING id, url, event_types, active, created_at",
            url,
            event_types,
            secret,
        )
        assert row is not None
        return row

    async def list(self) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT id, url, event_types, active, created_at "
                "FROM webhook_subscription ORDER BY created_at DESC"
            )
        )

    async def delete(self, webhook_id: uuid.UUID) -> bool:
        result = await self._conn.execute(
            "DELETE FROM webhook_subscription WHERE id = $1", webhook_id
        )
        return result.endswith("1")

    async def active_for_event(self, event_type: str) -> list[asyncpg.Record]:
        """Suscripciones activas que reciben este event_type (o todos si vacío)."""
        return list(
            await self._conn.fetch(
                "SELECT id, url, secret, event_types FROM webhook_subscription "
                "WHERE active AND (event_types = '{}' OR $1 = ANY(event_types))",
                event_type,
            )
        )
