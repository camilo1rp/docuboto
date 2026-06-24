# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio del historial de notificaciones (E16).

SQL crudo sobre `notifications`. La conexión llega con el search_path ya fijado
al schema del tenant (get_tenant_conn o acquire_tenant).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

from app.models.notification import Notification

_COLS = "id, recipient_email, subject, body, status, event_type, sent_at, " "attempts, last_error"


class NotificationRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(self, notification: Notification) -> None:
        await self._conn.execute(
            """
            INSERT INTO notifications
                (id, recipient_email, subject, body, status, event_type, sent_at,
                 attempts, last_error)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            uuid.UUID(notification.id),
            notification.recipient_email,
            notification.subject,
            notification.body,
            notification.status,
            notification.event_type,
            datetime.fromisoformat(notification.sent_at),
            notification.attempts,
            notification.last_error,
        )

    async def get_by_id(self, notification_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM notifications WHERE id = $1", notification_id
        )

    async def list_recent(self, limit: int) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM notifications ORDER BY sent_at DESC LIMIT $1",
                limit,
            )
        )
