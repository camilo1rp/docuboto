# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Consumidor ``intelligence-gateway`` (ADR-045, ADR-021).

Lee ``orpycamcp.document.events`` y ``orpycamcp.workflow.events``. Confirma
el mensaje solo cuando la decisión es cerrada. Un fallo de transporte o de
base deja el mensaje en la PEL; al quinto intento pasa al dead-letter y a
``evento_dead_letter``. Cada 30 segundos avanza una página de la carga
inicial de las entidades con el proveedor encendido.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import redis.exceptions as redis_exc
from orpycamcp_common import events
from orpycamcp_common.tenant import slug_valido

from app.core import paso
from app.core.config import settings
from app.core.consumidor import CONOCIDOS, atender, cargar_pagina
from app.core.database import acquire_tenant, get_pool
from app.core.redis_client import get_redis

try:
    from orpycamcp_common import audit as _audit

    _AUDIT = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT = False

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
WORKFLOW_STREAM = "orpycamcp.workflow.events"
STREAMS = (DOCUMENT_STREAM, WORKFLOW_STREAM)
DEADLETTER_STREAM = "orpycamcp.knowledge.deadletter"
GROUP = "intelligence-gateway"
CONSUMER = events.consumer_name("knowledge-service")
_SERVICE = "knowledge-service"
RECLAIM_INTERVAL = 30.0
_ENTIDADES = """
SELECT slug
  FROM public.tenants
 WHERE asistente_habilitado
   AND intelligence_provider = 'ppi'
"""
_LIBRO_MUERTO = """
INSERT INTO evento_dead_letter
    (event_id, event_type, correlation_id, tenant_slug,
     payload, failure, origin_stream, origin_group, dead_lettered_at)
VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, now())
ON CONFLICT (origin_group, event_id) DO NOTHING
RETURNING event_id
"""


class _Http:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)

    async def request(self, method: str, url: str, *, json: dict[str, Any]) -> Any:
        respuesta = await self._http.request(method, url, json=json)
        respuesta.raise_for_status()
        if not respuesta.content:
            return None
        return respuesta.json()

    async def cerrar(self) -> None:
        await self._http.aclose()


async def _handle(data: dict[str, Any], cliente: Any) -> None:
    envelope = events.parse_fields(data)
    if str(envelope.get("event_type") or "") not in CONOCIDOS:
        return
    slug = envelope.get("tenant_slug") or ""
    if not slug_valido(slug):
        return
    async with acquire_tenant(slug) as conn:
        await atender(conn, slug, envelope, cliente)


async def _entidades() -> list[str]:
    async with get_pool().acquire() as conn:
        filas = await conn.fetch(_ENTIDADES)
    return [str(fila["slug"]) for fila in filas]


async def _cargar(cliente: Any) -> None:
    for slug in await _entidades():
        try:
            async with acquire_tenant(slug) as conn:
                await cargar_pagina(conn, slug, cliente)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("la carga inicial de %s no avanzó", slug, exc_info=True)


async def _persist_dead_letter(envelope: dict[str, Any], failure_meta: dict[str, Any]) -> None:
    """Deja el poison pill en ``evento_dead_letter`` antes del ack.

    Sin tenant no hay schema donde escribirlo. Se anota en la auditoría
    pública y se confirma, para no reintentar un sobre que nunca va a
    poder guardarse.
    """
    slug = envelope.get("tenant_slug") or ""
    event_id = envelope.get("event_id") or ""
    if not slug_valido(slug):
        if _AUDIT:
            async with get_pool().acquire() as conn:
                await _audit.append(
                    conn,
                    tenant_slug=None,
                    service=_SERVICE,
                    action="evento.dead_letter",
                    object_type="evento",
                    object_ref=event_id,
                    actor="system",
                    canal="system",
                    payload={"failure": failure_meta, "envelope": envelope},
                )
        return

    payload = json.dumps(envelope, default=str)
    failure = json.dumps(failure_meta, default=str)
    async with acquire_tenant(slug) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                _LIBRO_MUERTO,
                event_id,
                envelope.get("event_type"),
                envelope.get("correlation_id"),
                slug,
                payload,
                failure,
                failure_meta.get("dead_letter_origin_stream"),
                failure_meta.get("dead_letter_group") or GROUP,
            )
            if row is not None and _AUDIT:
                await _audit.append(
                    conn,
                    tenant_slug=slug,
                    service=_SERVICE,
                    action="evento.dead_letter",
                    object_type="evento",
                    object_ref=event_id,
                    actor="system",
                    canal="system",
                    payload={"failure": failure_meta, "envelope": envelope},
                )


async def _reclamar(redis: Any, cliente: Any) -> None:
    async def handler(fields: dict[str, Any]) -> None:
        await _handle(fields, cliente)

    for stream in STREAMS:
        await events.reclaim_stale(
            redis,
            stream,
            GROUP,
            CONSUMER,
            handler=handler,
            min_idle_ms=60_000,
            max_deliveries=5,
            deadletter_stream=DEADLETTER_STREAM,
            on_dead_letter=_persist_dead_letter,
            status_name="intelligence-gateway",
        )


async def run() -> None:
    """Bucle del consumidor. Cancelar la tarea lo detiene."""
    redis = get_redis()
    transporte = _Http()
    cliente = paso.destino_de(settings.ppi_provider_url, transporte)
    try:
        for stream in STREAMS:
            await events.ensure_group(redis, stream, GROUP)
        ultima = time.monotonic() - RECLAIM_INTERVAL
        while True:
            try:
                messages = await redis.xreadgroup(
                    GROUP,
                    CONSUMER,
                    {stream: ">" for stream in STREAMS},
                    count=10,
                    block=5000,
                )
            except asyncio.CancelledError:
                raise
            except (TimeoutError, redis_exc.TimeoutError):
                messages = []
            except Exception:
                logger.warning("error en el bucle del consumidor", exc_info=True)
                await asyncio.sleep(5)
                messages = []

            for stream, entries in messages or []:
                for msg_id, data in entries:
                    try:
                        await _handle(data, cliente)
                        await redis.xack(stream, GROUP, msg_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        await events.record_failure(redis, GROUP, msg_id, exc)
                        logger.warning(
                            "el evento queda en la PEL para reintento",
                            exc_info=True,
                        )

            ahora = time.monotonic()
            if ahora - ultima >= RECLAIM_INTERVAL:
                try:
                    await _reclamar(redis, cliente)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("falló el barrido de la PEL", exc_info=True)
                try:
                    await _cargar(cliente)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("falló la carga inicial", exc_info=True)
                ultima = ahora
    finally:
        await transporte.cerrar()
