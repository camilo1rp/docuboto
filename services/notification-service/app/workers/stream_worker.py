# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from orpycamcp_common import events

from app.core.redis_client import get_redis
from app.services.event_consumer import process_document_event, process_event

WORKFLOW_STREAM = "orpycamcp.workflow.events"
DOCUMENT_STREAM = "orpycamcp.document.events"
GROUP = "notification-service"
CONSUMER = "notification-service-1"

# Cada stream se enruta a su handler. El de workflow consume el formato plano
# legado; el de document, el sobre canónico de orpycamcp_common (ADR-010).
_HANDLERS: dict[str, Callable[[dict], Awaitable[None]]] = {
    WORKFLOW_STREAM: process_event,
    DOCUMENT_STREAM: process_document_event,
}


def _name(stream: object) -> str:
    return stream.decode() if isinstance(stream, bytes | bytearray) else str(stream)


async def run() -> None:
    redis = get_redis()

    for stream in _HANDLERS:
        await events.ensure_group(redis, stream, GROUP)

    read_spec = {stream: ">" for stream in _HANDLERS}

    while True:
        try:
            messages = await redis.xreadgroup(GROUP, CONSUMER, read_spec, count=10, block=5000)
            for stream, entries in messages or []:  # type: ignore[misc]
                handler = _HANDLERS.get(_name(stream))
                for msg_id, data in entries:  # type: ignore[union-attr]
                    if handler is not None:
                        await handler(data)
                    await redis.xack(_name(stream), GROUP, msg_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)
