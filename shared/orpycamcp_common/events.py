# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sobre canónico de eventos sobre Redis Streams (00-contrato-eventos.md, ADR-010).

Todos los servicios publican y consumen con el **mismo sobre**, de modo que ningún
servicio reinvente el formato. Campos del sobre:

    event_id        UUID v4 único del evento (idempotencia)
    event_type      p. ej. "radicado.creado"
    version         versión del esquema del payload (int)
    tenant_slug     tenant dueño del evento
    occurred_at     ISO-8601 UTC
    actor           user_id que originó el evento (o None / "system")
    correlation_id  traza una cadena de eventos relacionados
    dedup_key       clave de deduplicación lógica (negocio)
    payload         dict con los datos específicos del evento

En Redis Streams los campos se almacenan planos; ``payload`` viaja como JSON.
Esta librería ofrece construir/serializar/parsear el sobre y helpers de grupo de
consumidores. El **loop** de consumo (con su tabla de idempotencia) lo arma cada
servicio, porque la idempotencia persistente vive en su propio schema.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# redis.asyncio.Redis — tipado laxo para no acoplar la firma al cliente concreto.
Redis = Any

logger = logging.getLogger(__name__)


def new_event_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_event(
    event_type: str,
    *,
    tenant_slug: str,
    payload: dict[str, Any] | None = None,
    version: int = 1,
    actor: str | None = None,
    correlation_id: str | None = None,
    dedup_key: str | None = None,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Construye el sobre canónico como dict."""
    return {
        "event_id": event_id or new_event_id(),
        "event_type": event_type,
        "version": version,
        "tenant_slug": tenant_slug,
        "occurred_at": occurred_at or _now_iso(),
        "actor": actor,
        "correlation_id": correlation_id,
        "dedup_key": dedup_key,
        "payload": payload or {},
    }


# ── Autenticidad del sobre ──────────────────────────────────────────────────
#
# Un sobre sin firma es una orden anónima. Los cuatro consumidores toman el
# `tenant_slug` DEL PROPIO SOBRE y lo usan para elegir el schema sobre el que
# escriben, así que quien pueda hacer `XADD` actúa como si fuera el productor
# legítimo, para cualquier entidad alojada, sin pasar por el gateway ni por
# ningún token: publicar una reclasificación a la baja bastaba para bajar de
# nivel un radicado reservado de otra entidad.
#
# La regex de `tenant_schema` cerraba la inyección de schema, no la suplantación
# del origen: son dos preguntas distintas y solo estaba respondida la primera.
#
# Se firma y se verifica AQUÍ porque `to_fields` y `parse_fields` son el paso
# único: todo productor pasa por la primera y todo consumidor por la segunda.
# Un control en el paso único no se puede olvidar al añadir el quinto consumidor.

_CAMPOS_FIRMADOS = (
    "event_id",
    "event_type",
    "version",
    "tenant_slug",
    "occurred_at",
    "actor",
    "correlation_id",
    "dedup_key",
    "payload",
)


class EventoNoAutentico(ValueError):
    """El sobre no trae firma válida del productor."""


def _secreto_de_eventos() -> str | None:
    return os.environ.get("EVENT_SIGNING_SECRET") or None


def _firma(campos: dict[str, str], secreto: str) -> str:
    # Se firma la CONCATENACIÓN CANÓNICA de los campos en orden fijo, con un
    # separador que no puede aparecer en un valor plano. Firmar el JSON tal cual
    # dependería del orden de las claves, que no está garantizado entre
    # versiones, y una firma que a veces no cuadra se acaba desactivando.
    base = "\x1e".join(campos.get(c, "") for c in _CAMPOS_FIRMADOS)
    return hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()


def firmar_campos(campos: dict[str, str]) -> dict[str, str]:
    """Añade `firma` a los campos del sobre, si hay secreto configurado."""
    secreto = _secreto_de_eventos()
    if secreto is None:
        return campos
    return {**campos, "firma": _firma(campos, secreto)}


def verificar_campos(campos: dict[str, str]) -> None:
    """Lanza `EventoNoAutentico` si el sobre no viene firmado por un productor.

    **Sin secreto configurado no verifica**, y es una decisión explícita, no un
    olvido: activarlo de golpe en un despliegue en marcha dejaría sin procesar
    todos los eventos ya encolados, incluidos los avisos a ciudadanos con plazo
    legal corriendo. El interruptor es la propia variable de entorno: quien la
    pone, la exige. La comprobación de que está puesta pertenece al arranque del
    servicio, no a cada mensaje.
    """
    secreto = _secreto_de_eventos()
    if secreto is None:
        return
    recibida = campos.get("firma") or ""
    esperada = _firma(campos, secreto)
    if not hmac.compare_digest(recibida, esperada):
        raise EventoNoAutentico(
            f"El evento {campos.get('event_id')!r} de tipo "
            f"{campos.get('event_type')!r} no trae firma válida del productor."
        )


def to_fields(event: dict[str, Any]) -> dict[str, str]:
    """Aplana el sobre a campos string para ``XADD`` (Redis Streams).

    Los campos salen FIRMADOS si el despliegue tiene secreto de eventos: es el
    paso único por el que pasa todo productor.
    """
    campos = {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "version": str(event["version"]),
        "tenant_slug": event["tenant_slug"],
        "occurred_at": event["occurred_at"],
        "actor": event.get("actor") or "",
        "correlation_id": event.get("correlation_id") or "",
        "dedup_key": event.get("dedup_key") or "",
        "payload": json.dumps(event.get("payload") or {}, default=str),
    }
    return firmar_campos(campos)


def parse_fields(fields: dict[Any, Any]) -> dict[str, Any]:
    """Reconstruye el sobre desde los campos de un mensaje de stream.

    Tolera claves/valores en ``bytes`` (cliente sin ``decode_responses``) o ``str``.
    """

    def _d(v: Any) -> str:
        return v.decode() if isinstance(v, bytes | bytearray) else v

    norm = {_d(k): _d(v) for k, v in fields.items()}
    verificar_campos(norm)
    return {
        "event_id": norm.get("event_id"),
        "event_type": norm.get("event_type"),
        "version": int(norm["version"]) if norm.get("version") else 1,
        "tenant_slug": norm.get("tenant_slug"),
        "occurred_at": norm.get("occurred_at"),
        "actor": norm.get("actor") or None,
        "correlation_id": norm.get("correlation_id") or None,
        "dedup_key": norm.get("dedup_key") or None,
        "payload": json.loads(norm["payload"]) if norm.get("payload") else {},
    }


async def publish(redis: Redis, stream: str, event: dict[str, Any]) -> str:
    """Publica el sobre en ``stream`` (XADD). Devuelve el id del mensaje."""
    msg_id = await redis.xadd(stream, to_fields(event))
    return msg_id.decode() if isinstance(msg_id, bytes | bytearray) else msg_id


async def emit(
    redis: Redis,
    stream: str,
    event_type: str,
    *,
    tenant_slug: str,
    payload: dict[str, Any] | None = None,
    **envelope: Any,
) -> tuple[str, dict[str, Any]]:
    """Atajo: construye y publica. Devuelve ``(msg_id, event)``."""
    event = build_event(event_type, tenant_slug=tenant_slug, payload=payload, **envelope)
    msg_id = await publish(redis, stream, event)
    return msg_id, event


async def ensure_group(redis: Redis, stream: str, group: str) -> None:
    """Crea el grupo de consumidores (idempotente, ``MKSTREAM``).

    Ignora el error ``BUSYGROUP`` si el grupo ya existe.
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as exc:  # redis.exceptions.ResponseError (BUSYGROUP)
        if "BUSYGROUP" not in str(exc):
            raise


# ---------------------------------------------------------------------------
# Supervisión del consumidor
# ---------------------------------------------------------------------------

#: Espera inicial y tope del reintento supervisado, en segundos.
SUPERVISOR_BACKOFF_INICIAL = 1.0
SUPERVISOR_BACKOFF_MAXIMO = 60.0

#: Estado de los consumidores supervisados de este proceso, por nombre.
#:
#: Existe para que la sonda de disponibilidad (`/health/ready`) pueda decir algo
#: verdadero sobre el consumidor. Sin esto, un servicio con el consumidor muerto
#: y reintentando responde exactamente igual que uno sano — que es justo el modo
#: de fallo que este bloque corrige.
_estado_consumidores: dict[str, dict[str, Any]] = {}


def consumer_status() -> dict[str, dict[str, Any]]:
    """Instantánea del estado de los consumidores supervisados.

    Por consumidor: ``corriendo`` (bool), ``reinicios`` (int) y ``ultimo_error``
    (texto o ``None``). ``reinicios`` creciendo con ``corriendo`` en ``True`` es
    un consumidor que rebota — sano en apariencia, inútil en la práctica.
    """
    return {nombre: dict(datos) for nombre, datos in _estado_consumidores.items()}


async def _supervisar(
    fabrica: Callable[[], Awaitable[None]],
    nombre: str,
    log: logging.Logger,
    backoff_inicial: float,
    backoff_maximo: float,
) -> None:
    """Reejecuta ``fabrica()`` indefinidamente con espera exponencial."""
    espera = backoff_inicial
    estado = _estado_consumidores.setdefault(
        nombre, {"corriendo": False, "reinicios": 0, "ultimo_error": None}
    )
    while True:
        try:
            estado["corriendo"] = True
            await fabrica()
            # Un consumidor no debe terminar por su cuenta: si vuelve, es un
            # defecto. Se reinicia igualmente y se dice en voz alta.
            estado["ultimo_error"] = "terminó por su cuenta"
            log.error("El consumidor %s terminó por su cuenta; se reinicia", nombre)
        except asyncio.CancelledError:
            estado["corriendo"] = False
            log.info("Consumidor %s cancelado; cierre limpio", nombre)
            raise
        except Exception as exc:
            estado["ultimo_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("El consumidor %s murió; reintento en %.0fs", nombre, espera)
        estado["corriendo"] = False
        estado["reinicios"] += 1
        await asyncio.sleep(espera)
        espera = min(espera * 2, backoff_maximo)


def spawn_supervised(
    fabrica: Callable[[], Awaitable[None]],
    *,
    name: str,
    logger: logging.Logger | None = None,
    backoff_inicial: float = SUPERVISOR_BACKOFF_INICIAL,
    backoff_maximo: float = SUPERVISOR_BACKOFF_MAXIMO,
) -> asyncio.Task:
    """Lanza un consumidor bajo supervisión, en vez de con `create_task` a pelo.

    **El problema.** Los cuatro consumidores del sistema se lanzaban así::

        consumer_task = asyncio.create_task(document_consumer.run())

    Ningún `done_callback`, ningún supervisor. Y `run()` llama a `ensure_group`
    **antes** de entrar en su bucle, fuera de todo `try`: si Redis no está
    disponible en ese instante —el arranque en frío del despliegue, donde el
    contenedor de Redis puede tardar más que el del servicio, o un reinicio
    posterior— la excepción sale de `run()` y **mata la tarea**. El proceso sigue
    vivo, la API sigue respondiendo, el `/health` sigue en 200 y **nadie consume
    nada**. asyncio solo emite «Task exception was never retrieved» cuando el
    recolector de basura llega a la tarea, que puede ser mucho después o nunca.

    Lo que se pierde en cada caso no es genérico: en workflow, la distribución
    automática de la correspondencia entrante —los radicados se quedan en la
    bandeja de quien radicó, sin repartir—; en notification, los correos que
    nunca salen; en signature, el sellado; en knowledge, la ingesta que deja el
    índice semántico congelado en el pasado. Todo en silencio y sin que ningún
    reintento lo recupere, porque el consumidor ya no existe.

    **La corrección.** El supervisor reejecuta la corrutina completa —no el
    cuerpo del bucle—, de modo que `ensure_group` se vuelve a ejecutar en cada
    reinicio: tras un parpadeo de Redis el grupo se restablece solo. La espera
    crece exponencialmente hasta un minuto para no martillear un Redis caído, y
    cada muerte se registra con `logger.exception`, que es lo que convierte un
    fallo mudo en uno visible.

    La cancelación (apagado del `lifespan`) se propaga sin reintentar: es la
    única salida legítima del bucle.
    """
    log = logger or logging.getLogger(__name__)
    return asyncio.create_task(
        _supervisar(fabrica, name, log, backoff_inicial, backoff_maximo),
        name=f"supervisor:{name}",
    )


# ---------------------------------------------------------------------------
# Dead-letter / reclaim helpers
# ---------------------------------------------------------------------------


@dataclass
class ReclaimStats:
    """Counters returned by :func:`reclaim_stale` for observability."""

    reclaimed: int = 0
    reprocessed_ok: int = 0
    reprocessed_fail: int = 0
    dead_lettered: int = 0


async def trim_deadletter(redis: Redis, stream: str, *, max_age_ms: int) -> int:
    """XTRIM the DLQ buffer stream by MINID, dropping entries older than max_age_ms.

    Safe because the authoritative copy of every dead-letter lives in Postgres
    (evento_dead_letter + audit_log); a Redis entry whose persistence failed is
    re-dead-lettered within the reclaim window (minutes), far below max_age_ms,
    so anything older than the cutoff is guaranteed already mirrored.

    Args:
        redis:       Async Redis client.
        stream:      DLQ stream name (e.g. ``"orpycamcp.signature.deadletter"``).
        max_age_ms:  Horizon in milliseconds.  Entries with a Redis ID older than
                     ``now - max_age_ms`` are eligible for removal.

    Returns:
        Number of entries removed (0 if Redis returned None or nothing to trim).
    """
    # Clamp to non-negative: an absurdly large max_age_ms (misconfig) would make the
    # cutoff negative and the MINID invalid; floor at 0 → trims nothing (safe no-op).
    cutoff_ms = max(0, int(datetime.now(UTC).timestamp() * 1000) - max_age_ms)
    min_id = f"{cutoff_ms}-0"
    trimmed = await redis.xtrim(stream, minid=min_id, approximate=True)
    count = int(trimmed) if trimmed is not None else 0
    logger.info(
        "DLQ trim: stream=%s min_id=%s entries_removed=%d", stream, min_id, count
    )
    return count


async def trim_stream(redis: Redis, stream: str, *, max_age_ms: int) -> int:
    """Recorta un stream de DOMINIO por antigüedad. Gemelo de `trim_deadletter`.

    Los streams de dominio se publicaban sin `maxlen`, sin caducidad y sin que
    nadie los recortara nunca. El resultado era un almacén paralelo de datos
    personales y de contenido documental —el asunto y el nombre del ciudadano de
    cada radicación—, en claro, fuera del schema de la entidad, fuera de la Tabla
    de Retención Documental que gobierna todo lo demás, y sin caducar jamás. Un
    solo `XRANGE` devolvía el historial completo de radicaciones del despliegue.
    Es la misma clase de exposición que el respaldo en claro, en otro sitio.

    **Lo que esto NO arregla, y conviene tenerlo dicho**: reclasificar un
    radicado hacia arriba no puede alcanzar la copia ya publicada en el stream.
    Un registro de solo-anexar no se retracta. Lo que hace la caducidad es
    acotar la VENTANA durante la cual esa copia existe, que es la mitigación
    honesta; la otra mitad —no poner el contenido en el sobre— es un cambio de
    contrato con los consumidores y queda declarado como pendiente.

    El recorte es por antigüedad y no por número de entradas a propósito: con
    `MAXLEN` la ventana depende del volumen del despliegue, así que una entidad
    con mucho tráfico conservaría minutos y una tranquila, meses. Por tiempo la
    promesa es la misma para todas.
    """
    cutoff_ms = max(0, int(datetime.now(UTC).timestamp() * 1000) - max_age_ms)
    min_id = f"{cutoff_ms}-0"
    trimmed = await redis.xtrim(stream, minid=min_id, approximate=True)
    count = int(trimmed) if trimmed is not None else 0
    logger.info(
        "trim de stream de dominio: stream=%s min_id=%s entradas_eliminadas=%d",
        stream,
        min_id,
        count,
    )
    return count


async def reclaim_stale(
    redis: Redis,
    stream: str,
    group: str,
    consumer: str,
    *,
    handler: Any,
    min_idle_ms: int = 60_000,
    max_deliveries: int = 5,
    deadletter_stream: str,
    batch: int = 50,
    on_dead_letter: Any = None,
    on_reclaim: Any = None,
) -> ReclaimStats:
    """Reclaims stale PEL entries and routes poison-pills to a dead-letter stream.

    Strategy: XPENDING(IDLE) + XCLAIM instead of XAUTOCLAIM.  XAUTOCLAIM does
    not expose the delivery counter; XPENDING with ``idle`` filters by idle time
    without incrementing the counter, so we can inspect ``times_delivered``
    before deciding whether to retry or dead-letter.

    Cadence contract that callers must respect::

        min_idle_ms (60 s) > reclaim_interval (e.g. 30 s) > block_timeout (5 s)

    This guarantees a healthy consumer actively processing a message for up to
    55 s is never pre-empted.

    All ``failure_meta`` values are ``str`` for Redis Streams field compatibility
    (streams store everything as byte strings).

    Args:
        redis:             Async Redis client (``decode_responses=True`` recommended).
        stream:            Source stream name.
        group:             Consumer group name.
        consumer:          Consumer name used for XCLAIM ownership transfer.
        handler:           ``async (raw_fields: dict) -> None`` — the same handler
                           used in the normal consumer loop.  Receives the raw
                           field dict from XCLAIM (same format as XREADGROUP).
        min_idle_ms:       Minimum idle time in ms before a message is considered
                           stale.
        max_deliveries:    Delivery threshold for dead-lettering (inclusive).
        deadletter_stream: Stream where poison-pills are routed.
        batch:             Maximum PEL entries to inspect per call.
        on_dead_letter:    Optional ``async (envelope: dict, failure_meta: dict)
                           -> None`` callback that durably records the dead-letter
                           (e.g. Postgres + audit). It is awaited *after* the XADD
                           buffer write but *before* the XACK, and its success
                           **gates** the ack: if it raises, the original message is
                           NOT acked (stays in the PEL for retry on the next sweep),
                           so there is no dead-letter without a durable trace. When
                           ``None``, the message is acked right after the XADD.
        on_reclaim:        Optional ``async (msg_id: str) -> None`` callback,
                           called after a successful XCLAIM (before handler).
                           Errors are caught and logged.

    Returns:
        :class:`ReclaimStats` with per-outcome counters.
    """
    stats = ReclaimStats()

    # xpending_range returns list[dict] with keys:
    #   message_id, consumer, time_since_delivered, times_delivered
    # Using idle= filters to entries idle for at least min_idle_ms — these are
    # the candidates a healthy consumer is no longer processing.
    pending = await redis.xpending_range(
        stream, group, min="-", max="+", count=batch, idle=min_idle_ms
    )
    if not pending:
        return stats

    for entry in pending:
        msg_id: str = entry["message_id"]
        times_delivered: int = int(entry["times_delivered"])

        if times_delivered >= max_deliveries:
            # --- DEAD-LETTER (orphan at threshold, no fresh attempt this sweep) ---
            # Transfer ownership first; if another consumer already acked it
            # between XPENDING and XCLAIM, claimed will be empty (race).
            claimed = await redis.xclaim(stream, group, consumer, min_idle_ms, [msg_id])
            if not claimed:
                continue  # race: already acked — nothing to do

            _id, fields = claimed[0]
            envelope = parse_fields(fields)

            # All values are str — Redis Streams fields are byte strings.
            # dead_letter_last_error is "" here: no fresh attempt was made in
            # this sweep so the concrete error is unknown (R-5b limitation,
            # documented in ADR-021).
            failure_meta: dict[str, str] = {
                "dead_letter_reason": "max_deliveries_exceeded",
                "dead_letter_origin_stream": stream,
                "dead_letter_group": group,
                "dead_letter_consumer": consumer,
                "dead_letter_delivery_count": str(times_delivered),
                "dead_lettered_at": _now_iso(),
                "dead_letter_last_error": "",
            }

            # Step 1: publish to dead-letter stream (best-effort operational buffer).
            await redis.xadd(deadletter_stream, {**to_fields(envelope), **failure_meta})

            logger.error(
                "Dead-lettered event after %d deliveries: event_id=%s tenant=%s "
                "stream=%s group=%s msg_id=%s",
                times_delivered,
                envelope.get("event_id"),
                envelope.get("tenant_slug"),
                stream,
                group,
                msg_id,
            )

            # Step 2: durable persistence callback — gates the XACK (R-4).
            # If on_dead_letter is None, fall through to XACK immediately (no
            # durability requirement from this call site).
            # If on_dead_letter raises, the message stays in the PEL for the
            # next sweep: no dead-letter without a durable trace (R-4, ADR-021).
            if on_dead_letter is not None:
                try:
                    await on_dead_letter(envelope, failure_meta)
                except Exception:
                    logger.warning(
                        "on_dead_letter callback failed for msg_id=%s — "
                        "message remains in PEL (R-4: no xack without durable trace)",
                        msg_id,
                        exc_info=True,
                    )
                    continue  # skip xack; next PEL entry

            # Step 3: XACK only after durable trace is confirmed.
            await redis.xack(stream, group, msg_id)
            stats.dead_lettered += 1

        else:
            # --- RECLAIM AND REPROCESS ---
            claimed = await redis.xclaim(stream, group, consumer, min_idle_ms, [msg_id])
            if not claimed:
                continue  # race: already acked — nothing to do

            stats.reclaimed += 1

            if on_reclaim is not None:
                try:
                    await on_reclaim(msg_id)
                except Exception:
                    logger.warning(
                        "on_reclaim callback failed for msg_id=%s — continuing",
                        msg_id,
                        exc_info=True,
                    )

            _id, fields = claimed[0]
            try:
                await handler(fields)
                # A-1: ack ONLY on success — if handler fails the message stays
                # in the PEL and will be retried on the next sweep.  Once
                # times_delivered >= max_deliveries it is dead-lettered.
                await redis.xack(stream, group, msg_id)
                stats.reprocessed_ok += 1
            except Exception as exc:
                # Cap the captured error: verbose driver errors (e.g. asyncpg) could be
                # large and might carry internal detail; the message lands in the (tenant-
                # scoped) DLQ/audit trace, so bound it to keep the record tidy and avoid
                # transporting unbounded internals.
                last_error = str(exc)[:500]
                # R-5b: XCLAIM already incremented the delivery counter internally.
                # If the effective count (times_delivered + 1) reaches the threshold,
                # dead-letter in this same sweep so the concrete last error is
                # captured rather than waiting for the next XPENDING scan (which
                # would land on the direct DL branch with dead_letter_last_error="").
                effective_count = times_delivered + 1
                if effective_count >= max_deliveries:
                    envelope_dl = parse_fields(fields)
                    failure_meta_dl: dict[str, str] = {
                        "dead_letter_reason": "max_deliveries_exceeded",
                        "dead_letter_origin_stream": stream,
                        "dead_letter_group": group,
                        "dead_letter_consumer": consumer,
                        "dead_letter_delivery_count": str(effective_count),
                        "dead_lettered_at": _now_iso(),
                        "dead_letter_last_error": last_error,
                    }
                    # Step 1: publish to dead-letter stream (best-effort buffer).
                    await redis.xadd(
                        deadletter_stream,
                        {**to_fields(envelope_dl), **failure_meta_dl},
                    )
                    logger.error(
                        "Dead-lettered event after %d deliveries (fresh attempt failed): "
                        "event_id=%s tenant=%s stream=%s group=%s msg_id=%s",
                        effective_count,
                        envelope_dl.get("event_id"),
                        envelope_dl.get("tenant_slug"),
                        stream,
                        group,
                        msg_id,
                        exc_info=True,
                    )
                    # Step 2: durable persistence callback — gates the XACK (R-4).
                    # Same contract as the direct-DL path above: callback failure
                    # means no XACK; the message stays in PEL for the next sweep.
                    _dl_committed = True
                    if on_dead_letter is not None:
                        try:
                            await on_dead_letter(envelope_dl, failure_meta_dl)
                        except Exception:
                            logger.warning(
                                "on_dead_letter callback failed for msg_id=%s — "
                                "message remains in PEL (R-4: no xack without durable trace)",
                                msg_id,
                                exc_info=True,
                            )
                            _dl_committed = False
                    # Step 3: XACK only after durable trace is confirmed.
                    if _dl_committed:
                        await redis.xack(stream, group, msg_id)
                        stats.dead_lettered += 1
                else:
                    logger.warning(
                        "Reprocess failed for msg_id=%s (times_delivered=%d) — "
                        "message remains in PEL for future retry or dead-lettering",
                        msg_id,
                        times_delivered,
                        exc_info=True,
                    )
                stats.reprocessed_fail += 1

    return stats
