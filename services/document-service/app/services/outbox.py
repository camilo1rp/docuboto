# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Outbox transaccional de eventos de dominio (AREA-09-05).

Publicar un evento DESPUÉS del commit y tragarse el fallo deja una ventana en la
que el cambio existe y el evento no. Para la reclasificación esa ventana es una
fuga: knowledge-service deriva el nivel de sus fragmentos solo del evento.

El patrón, en tres piezas:

1. `encolar` — dentro de la `conn.transaction()` del cambio: inserta la fila en
   `eventos_salientes`. Si la tx revierte, el evento no existe.
2. `enviar` — camino rápido tras el commit: XADD y marca `enviado_at`. Un fallo se
   registra y NO revierte nada: la fila sigue pendiente.
3. `retransmitir` — respaldo (lo invoca el barrido por tenant): reenvía lo que
   quedó pendiente pasada una gracia, con `SKIP LOCKED` para que dos réplicas no
   tomen la misma fila. At-least-once: el consumidor deduplica por `event_id`.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from orpycamcp_common import events

logger = logging.getLogger(__name__)

#: Un pendiente más joven que esto lo está atendiendo aún el camino rápido de su
#: propio request: reenviarlo ya duplicaría el mensaje sin necesidad.
GRACIA_SEGUNDOS = 60
#: Lo ya enviado se conserva este tiempo (diagnóstico) y luego se retira.
RETENCION_ENVIADOS_DIAS = 7


async def encolar(
    conn: asyncpg.Connection,
    *,
    stream: str,
    event_type: str,
    tenant_slug: str,
    actor: str | None,
    dedup_key: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Inserta el evento en la tx en curso y devuelve el sobre ya construido."""
    event = events.build_event(
        event_type,
        tenant_slug=tenant_slug,
        payload=payload,
        actor=actor,
        dedup_key=dedup_key,
        event_id=str(uuid.uuid4()),
        occurred_at=datetime.now(UTC).isoformat(),
    )
    await conn.execute(
        "INSERT INTO eventos_salientes "
        "(event_id, stream, tipo, tenant_slug, actor, dedup_key, payload, occurred_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)",
        event["event_id"],
        stream,
        event_type,
        tenant_slug,
        actor,
        dedup_key,
        json.dumps(payload, default=str),
        datetime.fromisoformat(event["occurred_at"]),
    )
    # El `stream` viaja junto al sobre para el camino rápido.
    return {"stream": stream, "event": event}


async def enviar(
    conn: asyncpg.Connection, redis: Any | None, pendiente: dict[str, Any]
) -> bool:
    """Camino rápido tras el commit. Nunca lanza: el respaldo lo recoge."""
    if redis is None:
        return False
    event = pendiente["event"]
    try:
        await events.publish(redis, pendiente["stream"], event)
        await conn.execute(
            "UPDATE eventos_salientes SET enviado_at = now(), intentos = intentos + 1 "
            "WHERE event_id = $1",
            event["event_id"],
        )
        return True
    except Exception:  # noqa: BLE001 — la fila queda pendiente y el barrido la reenvía
        logger.warning(
            "outbox: no se pudo publicar %s (%s); queda pendiente para el relé",
            event.get("event_type"),
            event.get("dedup_key"),
            exc_info=True,
        )
        return False


RECLASIFICADO = "document.radicado.reclassified"


async def _con_nivel_vivo(conn: asyncpg.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Sustituye `nivel_nuevo` por el nivel VIGENTE del radicado al reenviar.

    El relé reenvía tarde: si entre el encolado y el reenvío hubo otra
    reclasificación (A pendiente por un fallo de Redis, B ya publicada), reenviar el
    valor ORIGINAL de A haría que knowledge —que fija el nivel exacto— bajara el
    fragmento y lo sirviera a quien no debe. La verdad es `radicados.nivel_seguridad`,
    leída en la misma tx del relé: el reenvío tardío queda inocuo por construcción.
    """
    vivo = await conn.fetchval(
        "SELECT nivel_seguridad FROM radicados WHERE tracking_number = $1",
        payload.get("tracking_number"),
    )
    if vivo is None:
        return payload
    return {**payload, "nivel_nuevo": int(vivo)}


async def retransmitir(
    conn: asyncpg.Connection,
    redis: Any,
    *,
    gracia_segundos: int = GRACIA_SEGUNDOS,
    limite: int = 200,
) -> int:
    """Reenvía los pendientes con más de `gracia_segundos`. Devuelve cuántos salieron.

    Se detiene en el primer fallo de publicación (Redis caído: insistir con el
    resto solo acumularía errores) y deja el motivo en `ultimo_error`.
    """
    enviados = 0
    async with conn.transaction():
        filas = await conn.fetch(
            "SELECT id, event_id, stream, tipo, tenant_slug, actor, dedup_key, payload, "
            "       occurred_at "
            "  FROM eventos_salientes "
            " WHERE enviado_at IS NULL "
            "   AND creado_at < now() - make_interval(secs => $1::int) "
            " ORDER BY id LIMIT $2 FOR UPDATE SKIP LOCKED",
            gracia_segundos,
            limite,
        )
        for f in filas:
            payload = f["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if f["tipo"] == RECLASIFICADO:
                payload = await _con_nivel_vivo(conn, payload)
            event = events.build_event(
                f["tipo"],
                tenant_slug=f["tenant_slug"],
                payload=payload,
                actor=f["actor"],
                dedup_key=f["dedup_key"],
                event_id=f["event_id"],
                occurred_at=f["occurred_at"].isoformat(),
            )
            try:
                await events.publish(redis, f["stream"], event)
            except Exception as exc:  # noqa: BLE001
                await conn.execute(
                    "UPDATE eventos_salientes SET intentos = intentos + 1, "
                    "ultimo_error = $2 WHERE id = $1",
                    f["id"],
                    str(exc)[:500],
                )
                logger.warning("outbox: el relé no pudo publicar %s", f["event_id"], exc_info=True)
                break
            await conn.execute(
                "UPDATE eventos_salientes SET enviado_at = now(), intentos = intentos + 1, "
                "ultimo_error = NULL WHERE id = $1",
                f["id"],
            )
            enviados += 1
    await conn.execute(
        "DELETE FROM eventos_salientes WHERE enviado_at IS NOT NULL "
        "AND enviado_at < now() - make_interval(days => $1::int)",
        RETENCION_ENVIADOS_DIAS,
    )
    return enviados
