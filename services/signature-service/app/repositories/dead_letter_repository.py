# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repository for evento_dead_letter (durable dead-letter table, R-4 / ADR-021).

Used exclusively by document_consumer._persist_dead_letter; the INSERT must
be called inside an open transaction so that the audit_log row is written
atomically together with the dead-letter row.
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

    # ------------------------------------------------------------------
    # Inspección y replay (ADR-021 Fase 5, PERM_DLQ_ADMIN)
    # ------------------------------------------------------------------

    async def list_pending(
        self,
        caller_slug: str,
        *,
        status: str = "pending",
        origin_stream: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[asyncpg.Record], int]:
        """Lista resúmenes de dead-letters (sin payload ni detalle de fallo).

        Predicado de tenant EXPLÍCITO (``tenant_slug = $1``) además del
        search_path, defensa en profundidad contra fuga cross-tenant. Devuelve
        ``(rows, total)``; el total se obtiene con la ventana ``COUNT(*) OVER()``
        para no revelar el número de filas ocultas. Las columnas devueltas NO
        incluyen ``payload`` ni ``failure`` (evita filtrar existencia de objetos
        clasificados a través del resumen).
        """
        params: list[object] = [caller_slug, status]
        extra = ""
        if origin_stream is not None:
            params.append(origin_stream)
            extra = f" AND origin_stream = ${len(params)}"
        limit_ph = len(params) + 1
        offset_ph = len(params) + 2
        params.extend([limit, offset])
        sql = (
            "SELECT event_id, event_type, origin_stream, origin_group, status, "
            "       dead_lettered_at, resolved_at, COUNT(*) OVER() AS total_count "
            "FROM evento_dead_letter "
            "WHERE tenant_slug = $1 AND status = $2"
            f"{extra} "
            "ORDER BY dead_lettered_at DESC "
            f"LIMIT ${limit_ph} OFFSET ${offset_ph}"
        )
        rows = await self._conn.fetch(sql, *params)
        total = int(rows[0]["total_count"]) if rows else 0
        return list(rows), total

    async def get_by_event_id(
        self, event_id: str, caller_slug: str
    ) -> asyncpg.Record | None:
        """Lectura simple (sin lock) de una fila con predicado de tenant."""
        return await self._conn.fetchrow(
            "SELECT * FROM evento_dead_letter WHERE event_id = $1 AND tenant_slug = $2",
            event_id,
            caller_slug,
        )

    async def get_for_update(
        self, event_id: str, caller_slug: str
    ) -> asyncpg.Record | None:
        """SELECT … FOR UPDATE con predicado de tenant (replay atómico, H7).

        Debe llamarse dentro de una transacción abierta: bloquea la fila para que
        dos admins no reprocesen el mismo evento en paralelo.
        """
        return await self._conn.fetchrow(
            "SELECT * FROM evento_dead_letter "
            "WHERE event_id = $1 AND tenant_slug = $2 FOR UPDATE",
            event_id,
            caller_slug,
        )

    async def transition_status(
        self, event_id: str, caller_slug: str, new_status: str
    ) -> int:
        """Transición pending → new_status (resolved/discarded). Idempotencia por
        concurrencia: el WHERE exige ``status = 'pending'`` de modo que un segundo
        admin que llegue tarde obtiene 0 filas (el caller lo traduce a 409).

        Devuelve el número de filas afectadas (0 = ya cerrado por otro admin).
        """
        tag: str = await self._conn.execute(
            "UPDATE evento_dead_letter "
            "SET status = $3, resolved_at = now() "
            "WHERE event_id = $1 AND tenant_slug = $2 AND status = 'pending'",
            event_id,
            caller_slug,
            new_status,
        )
        return int(tag.split()[-1])
