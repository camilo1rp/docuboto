# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repository for evento_dead_letter (durable dead-letter table, R-4 / ADR-021 Fase 3).

Used exclusively by stream_worker._persist_dead_letter; the INSERT must be called
inside an open transaction so that the audit_log row is written atomically together
with the dead-letter row.
"""

from __future__ import annotations

import asyncpg


class DeadLetterRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert_if_new(
        self,
        *,
        event_id: str,
        event_type: str | None,
        correlation_id: str | None,
        tenant_slug: str,
        payload_json: str,
        failure_json: str,
        origin_stream: str | None,
        origin_group: str | None,
    ) -> asyncpg.Record | None:
        """Insert a dead-letter row; return the row if inserted, None on conflict.

        ON CONFLICT (event_id) DO NOTHING makes the insert idempotent: if the
        same event_id has already been persisted (e.g. XADD ok but XACK failed
        causing a redelivery) the second call is a no-op and returns None.
        Callers must skip the audit.append when None is returned.
        """
        return await self._conn.fetchrow(
            """
            INSERT INTO evento_dead_letter
                (event_id, event_type, correlation_id, tenant_slug,
                 payload, failure, origin_stream, origin_group, dead_lettered_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, now())
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            event_id,
            event_type,
            correlation_id,
            tenant_slug,
            payload_json,
            failure_json,
            origin_stream,
            origin_group,
        )
