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

import json
import uuid
from datetime import UTC, datetime
from typing import Any

# redis.asyncio.Redis — tipado laxo para no acoplar la firma al cliente concreto.
Redis = Any


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


def to_fields(event: dict[str, Any]) -> dict[str, str]:
    """Aplana el sobre a campos string para ``XADD`` (Redis Streams)."""
    return {
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


def parse_fields(fields: dict[Any, Any]) -> dict[str, Any]:
    """Reconstruye el sobre desde los campos de un mensaje de stream.

    Tolera claves/valores en ``bytes`` (cliente sin ``decode_responses``) o ``str``.
    """

    def _d(v: Any) -> str:
        return v.decode() if isinstance(v, bytes | bytearray) else v

    norm = {_d(k): _d(v) for k, v in fields.items()}
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
