# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repository for evento_dead_letter (durable dead-letter table, R-4 / ADR-021 Fase 3).

``insert_if_new`` is used exclusively by stream_worker._persist_dead_letter; the
INSERT must be called inside an open transaction so that the audit_log row is
written atomically together with the dead-letter row.

The remaining methods (list_visible, get_by_event_id,
get_for_update, transition_status, purge_terminal) back the admin
inspection/replay/discard router and the MVP-3 retention job (ADR-021 Fase 5
Inc.2). The clearance gate is evaluated in the query itself (``orpycamcp_common.deadletter``).
"""

from __future__ import annotations

import asyncpg
from orpycamcp_common import deadletter

# Columnas del resumen del listado (nunca ``payload`` ni ``failure``).
_SUMMARY_COLS = (
    "d.event_id, d.event_type, d.origin_stream, d.origin_group, d.status, "
    "d.dead_lettered_at, d.resolved_at"
)


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

        ON CONFLICT (origin_group, event_id) DO NOTHING makes the insert idempotent
        PER GROUP: if this group already persisted the same event_id (e.g. XADD ok
        but XACK failed causing a redelivery) the second call is a no-op and returns
        None. A DIFFERENT group parking the same event is NOT a conflict: the table is
        shared by four consumers and each must leave its own row and audit entry
        (AREA-10-07).
        Callers must skip the audit.append when None is returned.
        """
        return await self._conn.fetchrow(
            """
            INSERT INTO evento_dead_letter
                (event_id, event_type, correlation_id, tenant_slug,
                 payload, failure, origin_stream, origin_group, dead_lettered_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, now())
            ON CONFLICT (origin_group, event_id) DO NOTHING
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
    # Inspección y replay (ADR-021 Fase 5 Inc.2, PERM_DLQ_ADMIN)
    # ------------------------------------------------------------------
    #
    # `evento_dead_letter` es una TABLA FÍSICA COMPARTIDA dentro del schema del
    # tenant: signature-service, workflow-service y notification-service la crean
    # (misma DDL, `CREATE TABLE IF NOT EXISTS`) sobre la MISMA base de datos. Las
    # filas de cada servicio se distinguen por `origin_group` (el GROUP del
    # consumer Redis que las generó: "signature-service" / "workflow-service" /
    # "notification-service"; ver `app/workers/stream_worker.py` y
    # `orpycamcp_common.events.reclaim_stale`, que escribe
    # `dead_letter_group = group` en el `failure_meta`). TODA consulta de este
    # repositorio debe llevar el predicado `origin_group = $N` además del de
    # tenant: sin él, notification vería/reprocesaría/purgaría dead-letters de
    # signature o workflow y viceversa — mismo tenant (no es fuga cross-tenant)
    # pero handler equivocado (p. ej. un replay de notification reenviaría un
    # email a partir de un payload de firma o de distribución que no le
    # corresponde).
    #
    # A diferencia de signature/workflow, notification-service consume DOS
    # streams (`orpycamcp.workflow.events` y `orpycamcp.document.events`) bajo
    # UN solo consumer group — por tanto TODOS los dead-letters de este servicio
    # comparten `origin_group = "notification-service"`, y se distinguen entre
    # sí únicamente por `origin_stream` (ver DLQ_GROUP en app/core/constants.py).

    async def list_visible(
        self,
        caller_slug: str,
        origin_group: str,
        caller_clearance: int,
        *,
        status: str = "pending",
        origin_stream: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[asyncpg.Record], int]:
        """Página + total de dead-letters que el llamante PUEDE ver (AREA-10-08).

        El no-read-up (RF-SEG-08) vive en el ``WHERE`` de la consulta
        (``orpycamcp_common.deadletter``): el nivel se evalúa contra el radicado VIVO
        antes de paginar y de contar, así que ``total`` es exactamente lo visible.
        Predicados de tenant y ``origin_group`` explícitos (tabla compartida, ver nota
        de módulo). Las columnas devueltas NO incluyen ``payload`` ni ``failure``.
        """
        return await deadletter.list_visible(
            self._conn,
            tenant_slug=caller_slug,
            origin_group=origin_group,
            status=status,
            clearance=caller_clearance,
            columns=_SUMMARY_COLS,
            origin_stream=origin_stream,
            offset=offset,
            limit=limit,
        )

    async def get_by_event_id(
        self, event_id: str, caller_slug: str, origin_group: str, caller_clearance: int
    ) -> asyncpg.Record | None:
        """Lectura (sin lock) de una fila VISIBLE; ``None`` si no existe o no se alcanza."""
        return await deadletter.get_visible(
            self._conn,
            event_id=event_id,
            tenant_slug=caller_slug,
            origin_group=origin_group,
            clearance=caller_clearance,
        )

    async def get_for_update(
        self, event_id: str, caller_slug: str, origin_group: str, caller_clearance: int
    ) -> asyncpg.Record | None:
        """``FOR UPDATE OF d`` de una fila VISIBLE (replay/descarte atómicos, H7).

        Debe llamarse dentro de una transacción abierta. El mismo predicado de
        clearance que el listado: lo que no se ve no se puede bloquear, reprocesar ni
        descartar, y el llamante recibe ``None`` igual que para un ``event_id`` ajeno.
        """
        return await deadletter.get_visible(
            self._conn,
            event_id=event_id,
            tenant_slug=caller_slug,
            origin_group=origin_group,
            clearance=caller_clearance,
            for_update=True,
        )

    async def transition_status(
        self, event_id: str, caller_slug: str, new_status: str, origin_group: str
    ) -> int:
        """Transición pending → new_status (resolved/discarded). Idempotencia por
        concurrencia: el WHERE exige ``status = 'pending'`` de modo que un segundo
        admin que llegue tarde obtiene 0 filas (el caller lo traduce a 409).
        ``origin_group`` evita transicionar una fila que pertenece a otro
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
        terminal dead-letters belonging to OTHER services too, coupling their retention
        windows with notification's.

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
