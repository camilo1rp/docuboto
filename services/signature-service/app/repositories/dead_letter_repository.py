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
    #
    # `evento_dead_letter` es una TABLA FÍSICA COMPARTIDA dentro del schema del
    # tenant: tanto signature-service como workflow-service la crean (misma DDL,
    # `CREATE TABLE IF NOT EXISTS`) sobre la MISMA base de datos. Las filas de
    # cada servicio se distinguen por `origin_group` (el GROUP del consumer Redis
    # que las generó: "signature-service" / "workflow-service"; ver
    # `app/workers/document_consumer.py` y `orpycamcp_common.events.reclaim_stale`,
    # que escribe `dead_letter_group = group` en el `failure_meta`). TODA consulta
    # de este repositorio debe llevar el predicado `origin_group = $N` además del
    # de tenant: sin él, signature vería/reprocesaría/purgaría dead-letters de
    # workflow y viceversa — mismo tenant (no es fuga cross-tenant) pero handler
    # equivocado (p. ej. un replay de signature invocaría `apply_reclassification`
    # sobre un payload de distribución de workflow).

    async def list_pending(
        self,
        caller_slug: str,
        origin_group: str,
        *,
        status: str = "pending",
        origin_stream: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[asyncpg.Record], int]:
        """Lista resúmenes de dead-letters (sin payload ni detalle de fallo).

        Predicado de tenant EXPLÍCITO (``tenant_slug = $1``) además del
        search_path, defensa en profundidad contra fuga cross-tenant; ``origin_group
        = $3`` acota además a los dead-letters generados por ESTE servicio (tabla
        compartida con el otro consumidor, ver nota de módulo arriba). Devuelve
        ``(rows, total)``; el total se obtiene con la ventana ``COUNT(*) OVER()``
        para no revelar el número de filas ocultas. Las columnas devueltas NO
        incluyen ``payload`` ni ``failure`` (evita filtrar existencia de objetos
        clasificados a través del resumen).
        """
        params: list[object] = [caller_slug, status, origin_group]
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
            "WHERE tenant_slug = $1 AND status = $2 AND origin_group = $3"
            f"{extra} "
            "ORDER BY dead_lettered_at DESC "
            f"LIMIT ${limit_ph} OFFSET ${offset_ph}"
        )
        rows = await self._conn.fetch(sql, *params)
        total = int(rows[0]["total_count"]) if rows else 0
        return list(rows), total

    async def list_scoped_for_clearance(
        self,
        caller_slug: str,
        origin_group: str,
        *,
        status: str = "pending",
        origin_stream: str | None = None,
        cap: int = 1000,
    ) -> list[asyncpg.Record]:
        """Candidatas para el filtrado por-clearance del listado (ADR-021, seguimiento).

        A diferencia de :meth:`list_pending`, INCLUYE ``payload`` (necesario para que
        el router resuelva el nivel de seguridad de cada fila vía
        ``resolve_deadletter_level``) y NO aplica ``LIMIT``/``OFFSET`` de paginación:
        la paginación se aplica en la capa de aplicación **después** de filtrar por
        clearance (ver nota de diseño en el router), porque el número de filas
        visibles solo se conoce tras evaluar el nivel de cada una.

        ``cap`` acota la memoria/latencia del peor caso (tope de seguridad, no un
        tamaño de página): los dead-letters son de volumen bajo por diseño (fallos
        excepcionales tras agotar reintentos), así que un tope generoso no afecta
        el uso normal. El resumen expuesto al cliente sigue sin exponer ``payload``
        ni ``failure`` — el router los descarta antes de serializar.
        """
        params: list[object] = [caller_slug, status, origin_group]
        extra = ""
        if origin_stream is not None:
            params.append(origin_stream)
            extra = f" AND origin_stream = ${len(params)}"
        params.append(cap)
        sql = (
            "SELECT event_id, event_type, correlation_id, tenant_slug, origin_stream, "
            "       origin_group, status, dead_lettered_at, resolved_at, payload "
            "FROM evento_dead_letter "
            "WHERE tenant_slug = $1 AND status = $2 AND origin_group = $3"
            f"{extra} "
            "ORDER BY dead_lettered_at DESC "
            f"LIMIT ${len(params)}"
        )
        return list(await self._conn.fetch(sql, *params))

    async def get_by_event_id(
        self, event_id: str, caller_slug: str, origin_group: str
    ) -> asyncpg.Record | None:
        """Lectura simple (sin lock) de una fila con predicado de tenant + origin_group."""
        return await self._conn.fetchrow(
            "SELECT * FROM evento_dead_letter "
            "WHERE event_id = $1 AND tenant_slug = $2 AND origin_group = $3",
            event_id,
            caller_slug,
            origin_group,
        )

    async def get_for_update(
        self, event_id: str, caller_slug: str, origin_group: str
    ) -> asyncpg.Record | None:
        """SELECT … FOR UPDATE con predicado de tenant + origin_group (replay atómico, H7).

        Debe llamarse dentro de una transacción abierta: bloquea la fila para que
        dos admins no reprocesen el mismo evento en paralelo. ``origin_group``
        impide que el admin de un servicio bloquee/reprocese una fila que
        pertenece al otro consumidor (tabla compartida, ver nota de módulo).
        """
        return await self._conn.fetchrow(
            "SELECT * FROM evento_dead_letter "
            "WHERE event_id = $1 AND tenant_slug = $2 AND origin_group = $3 FOR UPDATE",
            event_id,
            caller_slug,
            origin_group,
        )

    async def transition_status(
        self, event_id: str, caller_slug: str, new_status: str, origin_group: str
    ) -> int:
        """Transición pending → new_status (resolved/discarded). Idempotencia por
        concurrencia: el WHERE exige ``status = 'pending'`` de modo que un segundo
        admin que llegue tarde obtiene 0 filas (el caller lo traduce a 409).
        ``origin_group`` evita transicionar una fila que pertenece al otro
        consumidor de la tabla compartida (ver nota de módulo).

        Devuelve el número de filas afectadas (0 = ya cerrado por otro admin, o
        la fila pertenece a otro origin_group y por tanto no es visible aquí).
        """
        tag: str = await self._conn.execute(
            "UPDATE evento_dead_letter "
            "SET status = $3, resolved_at = now() "
            "WHERE event_id = $1 AND tenant_slug = $2 AND origin_group = $4 "
            "AND status = 'pending'",
            event_id,
            caller_slug,
            new_status,
            origin_group,
        )
        return int(tag.split()[-1])

    async def purge_terminal(self, days: int, tenant_slug: str, origin_group: str) -> int:
        """Delete rows with terminal status ('resolved' or 'discarded') older than `days` days.

        Only terminal rows (resolved/discarded) are eligible for deletion: 'pending' rows
        represent unresolved failures that must wait for a human decision, so they are
        NEVER purged regardless of age.

        Invariant relied upon here: a row can only be terminal (resolved/discarded) if
        `transition_status` has already set `resolved_at`, which it always does in the
        same UPDATE that sets `status` — so `status IN ('resolved','discarded')` implies
        `resolved_at IS NOT NULL`. A row that somehow violated this (NULL `resolved_at`
        on a terminal row) would simply never satisfy `resolved_at < now() - interval`
        (NULL comparisons are never true in SQL) — fail-closed, not fail-open.

        `tenant_slug` and `origin_group` scope the purge to one tenant and one service's
        rows (tabla compartida, ver nota de módulo): without them this call would purge
        terminal dead-letters belonging to the OTHER service too, coupling the two
        services' retention windows.

        The `days` guard (fail-closed): if a caller passes 0 or a negative value the
        predicate `resolved_at < now() - make_interval(days => 0)` becomes
        `resolved_at < now()` — which would delete ALL terminal rows regardless of age.
        A misconfigured TTL must not silently widen the purge window, so we raise
        rather than execute.

        The interval is passed as a parameterised placeholder ($3) so the value never
        reaches the query as SQL text — no risk of injection even though `make_interval`
        accepts a plain integer argument.
        """
        if days < 1:
            raise ValueError(f"purge_terminal requires days >= 1, got {days!r}")
        tag: str = await self._conn.execute(
            "DELETE FROM evento_dead_letter "
            "WHERE status IN ('resolved', 'discarded') "
            "AND tenant_slug = $1 AND origin_group = $2 "
            "AND resolved_at < now() - make_interval(days => $3)",
            tenant_slug,
            origin_group,
            days,
        )
        return int(tag.split()[-1])
