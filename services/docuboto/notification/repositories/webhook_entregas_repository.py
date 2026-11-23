# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Constancia de entregas de webhooks (B4, ADR-018). Tabla append-only: solo INSERT y SELECT."""

from __future__ import annotations

import builtins
import uuid

import asyncpg

_COLS = (
    "id, entrega_id, fase, webhook_id, url_destino, url_sha256, event_id, event_type, "
    "intento, body_sha256, recortado, firmado, resultado, http_status, error, at"
)


class WebhookEntregasRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        entrega_id: uuid.UUID,
        fase: str,
        webhook_id: uuid.UUID,
        url_destino: str,
        url_sha256: str,
        event_id: str | None,
        event_type: str,
        intento: int,
        body_sha256: str,
        recortado: bool,
        firmado: bool,
        resultado: str | None = None,
        http_status: int | None = None,
        error: str | None = None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO webhook_entregas (entrega_id, fase, webhook_id, url_destino, url_sha256, "
            "event_id, event_type, intento, body_sha256, recortado, firmado, resultado, http_status, error) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
            entrega_id,
            fase,
            webhook_id,
            url_destino,
            url_sha256,
            event_id,
            event_type,
            intento,
            body_sha256,
            recortado,
            firmado,
            resultado,
            http_status,
            error[:500] if error else None,
        )

    async def list_by_webhook(
        self, webhook_id: uuid.UUID, *, limit: int, offset: int
    ) -> builtins.list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM webhook_entregas WHERE webhook_id = $1 "
                "ORDER BY at DESC, id DESC LIMIT $2 OFFSET $3",
                webhook_id,
                limit,
                offset,
            )
        )

    async def count_by_webhook(self, webhook_id: uuid.UUID) -> int:
        return int(
            await self._conn.fetchval(
                "SELECT count(*) FROM webhook_entregas WHERE webhook_id = $1", webhook_id
            )
        )
