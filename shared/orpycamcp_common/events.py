# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sobre canónico de eventos y su bus en PostgreSQL (00-contrato-eventos.md, ADR-010, ADR-049).

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

El sobre se guarda PLANO y FIRMADO (``to_fields``) en la tabla ``eventos`` del
schema de la entidad, en la MISMA transacción que el cambio de dominio (ADR-049):
si la transacción revierte, el evento no existe, y no hay outbox que reenviar.

Cada grupo consumidor toma eventos insertando su fila en ``evento_entregas``
(``ON CONFLICT DO NOTHING``: dos consumidores del mismo grupo nunca toman el
mismo) y los confirma fijando ``confirmado_en``. Lo tomado y sin confirmar es el
equivalente de las entregas pendientes de un broker, y :func:`reclaim_stale` lo reintenta o lo aparca.
El bucle que recorre las entidades vive en ``docuboto.core.bus``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from orpycamcp_common.request_log import canal_de_fondo

# asyncpg.Connection — tipado laxo: la librería compartida no importa asyncpg.
Conn = Any

#: Canal de `NOTIFY` que despierta a los consumidores. Lleva el slug de la
#: entidad, NUNCA el evento: el límite de 8.000 bytes de `NOTIFY` rompería un
#: sobre grande en silencio (ADR-049).
CANAL = "eventos"

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
# escriben, así que quien pueda insertar en el bus actúa como si fuera el productor
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
    """Aplana el sobre a campos string: la forma que se guarda en ``eventos.sobre``.

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
    """Reconstruye y VERIFICA el sobre desde sus campos planos.

    Tolera claves/valores en ``bytes`` o ``str``.
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


def _campos(sobre: Any) -> dict[str, str]:
    """`eventos.sobre` llega como texto JSON o ya decodificado, según el códec."""
    return json.loads(sobre) if isinstance(sobre, str | bytes | bytearray) else dict(sobre)


async def reloj_bd(conn: Conn) -> datetime:
    """Instante del reloj de LA BASE (``clock_timestamp()``), en la transacción en curso.

    Un solo reloj para todas las réplicas: dos reclasificaciones del mismo
    radicado quedan ordenadas como ocurrieron, aunque los procesos tengan el
    reloj desviado.
    """
    instante: datetime = await conn.fetchval("SELECT clock_timestamp()")
    return instante


async def publish(conn: Conn, stream: str, event: dict[str, Any]) -> int:
    """Publica el sobre en ``stream`` sobre la conexión del llamante. Devuelve el `seq`.

    Va en la transacción que tenga abierta ``conn``: es lo que hace que el evento
    y el hecho sean el mismo commit. El `NOTIFY` también es transaccional —sale
    al confirmar, y no sale si se revierte—, así que nunca despierta a nadie por
    un evento que no existe.
    """
    seq = await conn.fetchval(
        "INSERT INTO eventos (stream, event_id, sobre) VALUES ($1, $2, $3::jsonb) RETURNING seq",
        stream,
        str(event["event_id"]),
        json.dumps(to_fields(event)),
    )
    await conn.execute("SELECT pg_notify($1, $2)", CANAL, str(event["tenant_slug"]))
    return int(seq)


async def emit(
    conn: Conn,
    stream: str,
    event_type: str,
    *,
    tenant_slug: str,
    payload: dict[str, Any] | None = None,
    **envelope: Any,
) -> dict[str, Any]:
    """Atajo: construye y publica. Devuelve el sobre.

    `occurred_at` sale del reloj de LA BASE (`clock_timestamp()`) salvo que el
    llamante lo dé: el instante del hecho es el de la transacción que lo
    escribe, no el de un proceso con el reloj desviado (T13).
    """
    if "occurred_at" not in envelope:
        envelope["occurred_at"] = (await reloj_bd(conn)).astimezone(UTC).isoformat()
    event = build_event(event_type, tenant_slug=tenant_slug, payload=payload, **envelope)
    await publish(conn, stream, event)
    return event


async def leer(
    conn: Conn, group: str, streams: list[str] | tuple[str, ...], *, limite: int = 10
) -> list[tuple[str, dict[str, str]]]:
    """Toma hasta ``limite`` eventos que ``group`` no ha tomado aún, en orden de `seq`.

    Tomarlos es insertar su fila en ``evento_entregas``; ``ON CONFLICT DO
    NOTHING`` hace que dos consumidores del mismo grupo no tomen el mismo
    evento. Devuelve ``[(event_id, campos_planos)]`` — los campos tal como los
    espera ``parse_fields``, que es quien verifica la firma.

    No hay cursor por `seq` a propósito: los `seq` se reparten antes del commit,
    así que una transacción que confirma tarde con un `seq` menor quedaría
    detrás de un cursor que ya pasó, y su evento no se entregaría nunca.
    """
    filas = await conn.fetch(
        """
        WITH candidatos AS (
            SELECT e.seq, e.event_id
              FROM eventos e
             WHERE e.stream = ANY($2::text[])
               AND NOT EXISTS (
                   SELECT 1 FROM evento_entregas d
                    WHERE d.group_name = $1 AND d.event_id = e.event_id)
             ORDER BY e.seq
             LIMIT $3
        ), tomados AS (
            INSERT INTO evento_entregas (group_name, event_id, seq)
            SELECT $1, event_id, seq FROM candidatos
            ON CONFLICT DO NOTHING
            RETURNING event_id, seq
        )
        SELECT e.event_id, e.sobre
          FROM eventos e JOIN tomados t ON t.event_id = e.event_id
         ORDER BY t.seq
        """,
        group,
        list(streams),
        limite,
    )
    return [(f["event_id"], _campos(f["sobre"])) for f in filas]


async def ack(conn: Conn, group: str, event_id: str) -> None:
    """Confirma la entrega: el grupo no lo vuelve a ver y se olvida su último error."""
    await conn.execute(
        "UPDATE evento_entregas SET confirmado_en = now() "
        "WHERE group_name = $1 AND event_id = $2",
        group,
        event_id,
    )
    await conn.execute(
        "DELETE FROM evento_ultimo_error WHERE group_name = $1 AND event_id = $2",
        group,
        event_id,
    )


#: Vida del último error por entrega: mucho más que el horizonte de
#: reclamación (minutos); se purga con :func:`purgar`.
LASTERR_TTL_SECONDS = 24 * 3600
LASTERR_DESCONOCIDO = "desconocido: sin intento en este barrido"


async def record_failure(conn: Conn, group: str, event_id: str, exc: BaseException) -> None:
    """Recuerda el último error de una entrega (R-5b).

    Sin esto, una entrega que muere sin intento fresco llega a la rama
    «huérfana» de :func:`reclaim_stale` y se aparcaría con ``last_error`` vacío.
    Best-effort: nunca rompe el bucle del consumidor.
    """
    try:
        await conn.execute(
            """
            INSERT INTO evento_ultimo_error (group_name, event_id, mensaje)
            VALUES ($1, $2, $3)
            ON CONFLICT (group_name, event_id)
            DO UPDATE SET mensaje = EXCLUDED.mensaje, ocurrido_en = now()
            """,
            group,
            event_id,
            f"{type(exc).__name__}: {exc}"[:500],
        )
    except Exception:
        logger.debug("No se pudo registrar el último error de %s", event_id, exc_info=True)


async def _recall_failure(conn: Conn, group: str, event_id: str) -> str:
    try:
        valor = await conn.fetchval(
            "SELECT mensaje FROM evento_ultimo_error WHERE group_name = $1 AND event_id = $2",
            group,
            event_id,
        )
    except Exception:
        return LASTERR_DESCONOCIDO
    return valor if isinstance(valor, str) and valor else LASTERR_DESCONOCIDO


async def purgar(conn: Conn, *, dias: int) -> int:
    """Borra los eventos de más de ``dias`` días que ningún grupo tiene sin confirmar.

    Eventos y entregas se purgan JUNTOS y con la misma política: la
    deduplicación de un grupo es su fila en ``evento_entregas``, así que borrar
    la entrega y dejar el evento lo haría reprocesar (ADR-049, «la ventana de
    la deduplicación se ata a la purga»). Lo que un grupo tiene sin confirmar no
    se borra: está en reclamación o camino del dead-letter.

    Es también la caducidad de los datos personales que viajan en el sobre:
    la ventana es la misma para todas las entidades, sea cual sea su volumen.
    """
    borrados = await conn.fetchval(
        """
        WITH viejos AS (
            SELECT e.event_id FROM eventos e
             WHERE e.publicado_en < now() - make_interval(days => $1)
               AND NOT EXISTS (
                   SELECT 1 FROM evento_entregas d
                    WHERE d.event_id = e.event_id AND d.confirmado_en IS NULL)
        ), entregas AS (
            DELETE FROM evento_entregas WHERE event_id IN (SELECT event_id FROM viejos)
        ), borrados AS (
            DELETE FROM eventos WHERE event_id IN (SELECT event_id FROM viejos)
            RETURNING 1
        )
        SELECT count(*) FROM borrados
        """,
        dias,
    )
    await conn.execute(
        "DELETE FROM evento_ultimo_error WHERE ocurrido_en < now() - make_interval(secs => $1)",
        LASTERR_TTL_SECONDS,
    )
    return int(borrados or 0)


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
    # Un consumidor es un proceso de fondo: lo que asienta no viene de una petición
    # (AREA-03-07). La tarea tiene su propia copia del contexto.
    canal_de_fondo()
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

    **El problema.** Un consumidor lanzado con `create_task` a pelo, sin
    supervisor, muere en silencio si su primera consulta falla —el arranque en
    frío, con la base todavía sin aceptar conexiones—: el proceso sigue vivo, la
    API responde, `/health` sigue en 200 y **nadie consume nada**. Lo que se
    pierde no es genérico: la distribución automática de la correspondencia
    entrante, los correos que nunca salen, el sellado, la ingesta de
    inteligencia.

    **La corrección.** El supervisor reejecuta la corrutina completa —no el
    cuerpo del bucle— con espera exponencial hasta un minuto, y cada muerte se
    registra con `logger.exception`, que es lo que convierte un fallo mudo en
    uno visible.

    La cancelación (apagado del `lifespan`) se propaga sin reintentar: es la
    única salida legítima del bucle.
    """
    log = logger or logging.getLogger(__name__)
    return asyncio.create_task(
        _supervisar(fabrica, name, log, backoff_inicial, backoff_maximo),
        name=f"supervisor:{name}",
    )


# ---------------------------------------------------------------------------
# Reclamación y dead-letter
# ---------------------------------------------------------------------------


@dataclass
class ReclaimStats:
    """Counters returned by :func:`reclaim_stale` for observability."""

    reclaimed: int = 0
    reprocessed_ok: int = 0
    reprocessed_fail: int = 0
    dead_lettered: int = 0


async def reclaim_stale(
    conn: Conn,
    group: str,
    *,
    handler: Any,
    min_idle_s: int = 60,
    max_deliveries: int = 5,
    batch: int = 50,
    on_dead_letter: Any = None,
    status_name: str | None = None,
    consumer: str = "",
) -> ReclaimStats:
    """Reintenta las entregas atascadas de ``group`` y aparca las envenenadas.

    Una entrega está atascada si lleva más de ``min_idle_s`` sin confirmar. Se
    reclama con ``FOR UPDATE SKIP LOCKED`` —dos barridos no reclaman la misma—
    y la reclamación sube ``entregas``: equivale a ``XAUTOCLAIM``.

    - Si ya había llegado a ``max_deliveries`` antes de esta reclamación, se
      aparca sin intento nuevo, con el último error que dejó (R-5b).
    - Si no, se reintenta con ``handler(campos)``. Si falla y con esta entrega
      llega al tope, se aparca en el mismo barrido con el error fresco.

    Aparcar es llamar a ``on_dead_letter(envelope, failure_meta)``, y **solo si
    tiene éxito** se confirma la entrega (R-4, ADR-021): no hay dead-letter sin
    rastro durable. Si falla, la entrega sigue pendiente y el próximo barrido la
    vuelve a intentar.

    Cadencia que los llamantes respetan: ``min_idle_s`` (60) > intervalo del
    barrido (30) > espera del bucle (5), para no adelantarse a un consumidor
    sano que aún está procesando.

    ``conn`` llega con el ``search_path`` de la entidad y SIN transacción
    abierta: la reclamación tiene que quedar confirmada antes de que el handler,
    que abre sus propias conexiones, empiece.
    """
    stats = ReclaimStats()
    try:
        return await _reclaim_sweep(
            conn, group, stats,
            handler=handler, min_idle_s=min_idle_s, max_deliveries=max_deliveries,
            batch=batch, on_dead_letter=on_dead_letter, consumer=consumer,
        )
    finally:
        # `finally`: un barrido que revienta a medias también deja constancia de
        # lo que alcanzó a hacer (p. ej. aparcó 2 entregas antes del fallo).
        _record_sweep(status_name, stats)


def _record_sweep(status_name: str | None, stats: ReclaimStats) -> None:
    if not status_name:
        return
    estado = _estado_consumidores.setdefault(
        status_name, {"corriendo": False, "reinicios": 0, "ultimo_error": None}
    )
    estado["reclamados"] = stats.reclaimed
    estado["reprocesados_ok"] = stats.reprocessed_ok
    estado["reprocesados_fallidos"] = stats.reprocessed_fail
    estado["dead_lettered"] = stats.dead_lettered
    estado["dead_lettered_total"] = (
        int(estado.get("dead_lettered_total", 0)) + stats.dead_lettered
    )
    estado["ultimo_barrido"] = _now_iso()


async def _reclaim_sweep(
    conn: Conn,
    group: str,
    stats: ReclaimStats,
    *,
    handler: Any,
    min_idle_s: int,
    max_deliveries: int,
    batch: int,
    on_dead_letter: Any,
    consumer: str,
) -> ReclaimStats:
    reclamadas = await conn.fetch(
        """
        WITH atascadas AS (
            SELECT group_name, event_id FROM evento_entregas
             WHERE group_name = $1
               AND confirmado_en IS NULL
               AND reclamado_en < now() - make_interval(secs => $2)
             ORDER BY seq
             LIMIT $3
             FOR UPDATE SKIP LOCKED
        )
        UPDATE evento_entregas d
           SET reclamado_en = now(), entregas = d.entregas + 1
          FROM atascadas a
         WHERE d.group_name = a.group_name AND d.event_id = a.event_id
        RETURNING d.event_id, d.entregas,
                  (SELECT e.stream FROM eventos e WHERE e.event_id = d.event_id) AS stream,
                  (SELECT e.sobre FROM eventos e WHERE e.event_id = d.event_id) AS sobre
        """,
        group,
        min_idle_s,
        batch,
    )

    for fila in reclamadas:
        event_id: str = fila["event_id"]
        entregas: int = int(fila["entregas"])
        if fila["sobre"] is None:
            # El evento ya no existe (purgado): no hay nada que reintentar.
            await ack(conn, group, event_id)
            continue
        campos = _campos(fila["sobre"])
        stream = fila["stream"]

        def _meta(cuenta: int, ultimo_error: str, _stream: str = stream) -> dict[str, str]:
            return {
                "dead_letter_reason": "max_deliveries_exceeded",
                "dead_letter_origin_stream": _stream,
                "dead_letter_group": group,
                "dead_letter_consumer": consumer or group,
                "dead_letter_delivery_count": str(cuenta),
                "dead_lettered_at": _now_iso(),
                "dead_letter_last_error": ultimo_error,
            }

        if entregas - 1 >= max_deliveries:
            # Huérfana en el tope: sin intento nuevo, con el error que dejó.
            meta = _meta(entregas - 1, await _recall_failure(conn, group, event_id))
            if await _aparcar(conn, group, event_id, campos, meta, on_dead_letter):
                stats.dead_lettered += 1
            continue

        stats.reclaimed += 1
        try:
            await handler(campos)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stats.reprocessed_fail += 1
            await record_failure(conn, group, event_id, exc)
            if entregas >= max_deliveries:
                meta = _meta(entregas, str(exc)[:500])
                if await _aparcar(conn, group, event_id, campos, meta, on_dead_letter):
                    stats.dead_lettered += 1
            else:
                logger.warning(
                    "Reproceso fallido: event_id=%s group=%s entregas=%d — sigue pendiente",
                    event_id,
                    group,
                    entregas,
                    exc_info=True,
                )
            continue
        await ack(conn, group, event_id)
        stats.reprocessed_ok += 1

    return stats


async def _aparcar(
    conn: Conn,
    group: str,
    event_id: str,
    campos: dict[str, str],
    meta: dict[str, str],
    on_dead_letter: Any,
) -> bool:
    """Lleva una entrega al dead-letter. Confirma SOLO si el rastro durable quedó."""
    try:
        envelope = parse_fields(campos)
    except EventoNoAutentico:
        # Un sobre sin firma válida no es de un productor: no se aparca como si
        # lo fuera (la consola permitiría reproducirlo). Se confirma y se dice.
        logger.error("Evento sin firma válida descartado: event_id=%s group=%s", event_id, group)
        await ack(conn, group, event_id)
        return False
    logger.error(
        "Dead-letter tras %s entregas: event_id=%s tenant=%s stream=%s group=%s",
        meta["dead_letter_delivery_count"],
        event_id,
        envelope.get("tenant_slug"),
        meta["dead_letter_origin_stream"],
        group,
    )
    if on_dead_letter is not None:
        try:
            await on_dead_letter(envelope, meta)
        except Exception:
            logger.warning(
                "on_dead_letter falló para event_id=%s — la entrega sigue pendiente "
                "(R-4: no se confirma sin rastro durable)",
                event_id,
                exc_info=True,
            )
            return False
    await ack(conn, group, event_id)
    return True
