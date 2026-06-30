# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repository for notification_processed_events (idempotency / dedup, ADR-021 Fase 3).

Used by event_consumer handlers to gate external effects (email, webhooks) with
insert-then-send semantics: the event_id is claimed in Postgres before any external
effect is triggered.  If the INSERT conflicts (event already processed), the handler
skips silently — intentional dedup, not data loss.

The INSERT must NOT be called inside an open transaction from the caller; it executes
its own atomic write.  BD failures propagate to the caller (not caught here), causing
the message to remain in the PEL for retry by the reclaim sweep.
"""

from __future__ import annotations

import asyncpg


class ProcessedEventsRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def claim(self, event_id: str) -> bool:
        """Claim event_id atomically; return True if claimed (new event), False if duplicate.

        Uses INSERT … ON CONFLICT (event_id) DO NOTHING RETURNING event_id:
          - Returns a row   → INSERT was real → event is new → process it.
          - Returns None    → ON CONFLICT     → already processed → skip (dedup).

        BD failures (pool exhausted, connection lost, etc.) are NOT caught and
        propagate to the caller.  The caller (event_consumer handler) must let
        them propagate further so that A-1 in stream_worker leaves the message
        in the PEL for retry by reclaim_stale.
        """
        row = await self._conn.fetchrow(
            """
            INSERT INTO notification_processed_events (event_id)
            VALUES ($1)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            event_id,
        )
        return row is not None
