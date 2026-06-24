# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Worker: consume `orpycamcp.document.events` y distribuye automáticamente (E05).

Por cada `document.radicado.created` evalúa las reglas del tenant y autoasigna
el radicado (distribution.auto_distribute). Usa el sobre canónico de
orpycamcp_common y un grupo de consumidores propio.
"""

from __future__ import annotations

import asyncio
import logging

from orpycamcp_common import events

from app.core.database import acquire_tenant
from app.core.redis_client import get_redis
from app.services.distribution import auto_distribute

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
GROUP = "workflow-service"
CONSUMER = "workflow-service-1"


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    if envelope.get("event_type") != "document.radicado.created":
        return
    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}
    if not tenant_slug or not payload.get("id"):
        return
    async with acquire_tenant(tenant_slug) as conn:
        await auto_distribute(conn, tenant_slug, payload)


async def run() -> None:
    redis = get_redis()
    await events.ensure_group(redis, DOCUMENT_STREAM, GROUP)

    while True:
        try:
            messages = await redis.xreadgroup(
                GROUP, CONSUMER, {DOCUMENT_STREAM: ">"}, count=10, block=5000
            )
            for _stream, entries in messages or []:  # type: ignore[misc]
                for msg_id, data in entries:  # type: ignore[union-attr]
                    try:
                        await _handle(data)
                    except Exception:
                        logger.warning("Fallo distribuyendo evento", exc_info=True)
                    await redis.xack(DOCUMENT_STREAM, GROUP, msg_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)
