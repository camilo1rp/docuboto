# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El dead-letter del bus de eventos: una implementación para los cuatro grupos (ADR-049 §4).

Había tres copias —workflow, signature y notification— del repositorio, de la
retención y de la escritura del aparcamiento, más una cuarta escritura en línea en
la pasarela de inteligencia. Eran el mismo código con otro nombre de servicio.

- :class:`DeadLetterRepository`: la tabla ``evento_dead_letter`` (compartida por
  los grupos dentro del schema de la entidad, PK ``(origin_group, event_id)``). La
  visibilidad por clearance vive en el ``WHERE`` (``orpycamcp_common.deadletter``).
- :func:`persistir`: el ``on_dead_letter`` de todos los consumidores. Fila + asiento
  en una transacción; un error transitorio se propaga para que el bus NO confirme
  la entrega (R-4).
- :func:`purgar_terminales`: la retención de lo ya resuelto o descartado, una
  política para todos los grupos. La llama el barrido.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
from orpycamcp_common import audit, deadletter
from orpycamcp_common.database import acquire_tenant, get_pool
from orpycamcp_common.tenant import slug_valido

logger = logging.getLogger(__name__)


class ReplayFallido(Exception):
    """El reproceso de un dead-letter no se completó; el mensaje es apto para el usuario.

    Lo lanza el ``replay`` de un dominio cuando sabe decir qué falló sin exponer
    detalles internos (p. ej. «2 de 3 envíos fallaron»). La consola lo devuelve
    como 502 con ese texto; cualquier otra excepción da un 502 genérico. En los dos
    casos la transacción se revierte y el dead-letter sigue pendiente.
    """

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
        PER GROUP: if this group already persisted the same event_id (e.g. the
        insert committed but the delivery's confirmation did not) the second call is
        a no-op and returns None. A DIFFERENT group parking the same event is NOT a conflict: the table is
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
    # Inspección y replay (ADR-021 Fase 5, PERM_DLQ_ADMIN)
    # ------------------------------------------------------------------
    #
    # `evento_dead_letter` es una tabla COMPARTIDA por los cuatro grupos dentro del
    # schema de la entidad; las filas se distinguen por `origin_group`. TODA
    # consulta lleva `origin_group = $N` además del tenant: sin él, la consola de
    # un grupo vería, reprocesaría o purgaría los dead-letters de otro con el
    # handler equivocado.

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
        ``origin_group`` evita transicionar una fila que pertenece a otro grupo
        de la tabla compartida.

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

    async def purge_terminal(
        self, days: int, tenant_slug: str, origin_group: str | None = None
    ) -> int:
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

        `tenant_slug` scopes the purge to one tenant. `origin_group` is optional: the
        single retention pass (`purgar_terminales`) purges every group with one policy.

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
            "AND tenant_slug = $1 AND ($2::text IS NULL OR origin_group = $2) "
            "AND resolved_at < now() - make_interval(days => $3)",
            tenant_slug,
            origin_group,
            days,
        )
        return int(tag.split()[-1])


async def persistir(
    envelope: dict[str, Any], failure_meta: dict[str, Any], *, group: str, service: str
) -> None:
    """Aparca un evento: fila en ``evento_dead_letter`` + asiento, en una transacción.

    Se pasa al bus como ``on_dead_letter`` con ``functools.partial`` (``group`` y
    ``service`` del consumidor). Idempotente por grupo: si la fila ya existía no se
    repite el asiento.

    Un sobre sin entidad válida no tiene schema donde escribirse: se asienta bajo
    SYSTEM_TENANT en ``public.audit_log`` y se vuelve con normalidad, para que el
    bus confirme y no reintente para siempre algo que nunca podrá guardarse.

    Los errores transitorios NO se capturan: el bus deja la entrega pendiente y el
    próximo barrido lo reintenta (R-4: no hay dead-letter sin rastro durable).
    """
    tenant_slug = str(envelope.get("tenant_slug") or "")
    event_id = str(envelope.get("event_id") or "")
    if not slug_valido(tenant_slug):
        logger.error(
            "Dead-letter sin entidad válida (event_id=%s, group=%s): se asienta bajo "
            "SYSTEM_TENANT y se consume",
            event_id,
            group,
        )
        async with get_pool().acquire() as conn:
            await audit.append(
                conn,
                tenant_slug=None,  # → SYSTEM_TENANT (__system__)
                service=service,
                action="evento.dead_letter",
                object_type="evento",
                object_ref=event_id,
                actor="system",
                canal="system",
                payload={
                    "failure": failure_meta,
                    "envelope": envelope,
                    "note": "evento sin tenant_slug válido; registrado bajo SYSTEM_TENANT",
                },
            )
        return

    evt_payload: dict[str, Any] = envelope.get("payload") or {}
    registro = {
        "event_id": event_id,
        "event_type": envelope.get("event_type"),
        "correlation_id": envelope.get("correlation_id"),
        "tenant_slug": tenant_slug,
        "occurred_at": envelope.get("occurred_at"),
        "actor": envelope.get("actor"),  # el actor original se conserva (R-9)
        "payload": evt_payload,
    }
    asiento = {
        "failure": failure_meta,
        "envelope": registro,
        "business": {
            "tracking_number": evt_payload.get("tracking_number"),
            "doc_id": evt_payload.get("id"),
        },
    }
    async with acquire_tenant(tenant_slug) as conn:
        async with conn.transaction():
            fila = await DeadLetterRepository(conn).insert_if_new(
                event_id=event_id,
                event_type=envelope.get("event_type"),
                correlation_id=envelope.get("correlation_id"),
                tenant_slug=tenant_slug,
                payload_json=json.dumps(registro, default=str),
                failure_json=json.dumps(failure_meta, default=str),
                origin_stream=failure_meta.get("dead_letter_origin_stream"),
                origin_group=failure_meta.get("dead_letter_group") or group,
            )
            if fila is None:
                logger.debug(
                    "Dead-letter ya persistido (idempotente): event_id=%s group=%s",
                    event_id,
                    group,
                )
                return
            await audit.append(
                conn,
                tenant_slug=tenant_slug,
                service=service,
                action="evento.dead_letter",
                object_type="evento",
                object_ref=event_id,
                actor="system",  # R-9: acto del consumidor, no de una persona
                canal="system",
                payload=asiento,
            )
    logger.info(
        "Dead-letter persistido: event_id=%s tenant=%s group=%s type=%s",
        event_id,
        tenant_slug,
        group,
        envelope.get("event_type"),
    )


async def purgar_terminales(conn: asyncpg.Connection, tenant_slug: str, *, dias: int) -> int:
    """Retira lo resuelto o descartado de más de ``dias`` días, de todos los grupos.

    Lo pendiente no se toca nunca: espera una decisión humana. Cada resolución o
    descarte ya dejó su asiento en ``audit_log``, así que borrar la fila retira
    metadatos operativos, no la traza legal.
    """
    return await DeadLetterRepository(conn).purge_terminal(dias, tenant_slug)
