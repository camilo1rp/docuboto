# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Consumidor ``intelligence-gateway`` (ADR-045, ADR-021).

Lee ``orpycamcp.document.events`` y ``orpycamcp.workflow.events`` del bus en
PostgreSQL (ADR-049). Confirma la entrega solo cuando la decisión es cerrada.
Un fallo de transporte o de base la deja pendiente; a la quinta entrega pasa a
``evento_dead_letter``. Cada 30 segundos avanza una página de la carga
inicial de las entidades con el proveedor encendido, comprueba la
declaración firmada y reconcilia el libro y una página del inventario.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from orpycamcp_common import events
from orpycamcp_common.database import acquire_tenant, get_pool
from orpycamcp_common.tenant import slug_valido

from docuboto.config import knowledge as settings
from docuboto.core import bus, dead_letter
from knowledge.core import paso
from knowledge.core.conexion import asegurar
from knowledge.core.consumidor import CONOCIDOS, atender, cargar_pagina
from knowledge.core.reconciliacion import reconciliar
from knowledge.core.salida_http import TransporteHttp

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
WORKFLOW_STREAM = "orpycamcp.workflow.events"
STREAMS = (DOCUMENT_STREAM, WORKFLOW_STREAM)
GROUP = "intelligence-gateway"
_SERVICE = "knowledge-service"
#: Cadencia de la carga inicial, la conexión y la reconciliación.
PERIODO = 30.0
_ENTIDADES = """
SELECT slug
  FROM public.tenants
 WHERE asistente_habilitado
   AND intelligence_provider = 'ppi'
"""
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


async def _conectar(cliente: Any) -> None:
    for slug in await _entidades():
        try:
            async with acquire_tenant(slug) as conn:
                await asegurar(conn, slug, cliente)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("la conexión de %s no avanzó", slug, exc_info=True)


async def _reconciliar(cliente: Any) -> None:
    for slug in await _entidades():
        try:
            async with acquire_tenant(slug) as conn:
                await reconciliar(conn, slug, cliente)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("la reconciliación de %s no avanzó", slug, exc_info=True)


#: El `on_dead_letter` del bus: la escritura única del aparcamiento (ADR-049 §4).
_persist_dead_letter = partial(dead_letter.persistir, group=GROUP, service=_SERVICE)


async def _periodico(cliente: Any) -> None:
    """Conexión, una página de carga inicial y reconciliación, cada una aislada."""
    for nombre, paso_ in (
        ("la conexión", _conectar),
        ("la carga inicial", _cargar),
        ("la reconciliación", _reconciliar),
    ):
        try:
            await paso_(cliente)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("falló %s", nombre, exc_info=True)


async def run() -> None:
    """Bucle del consumidor. Cancelar la tarea lo detiene."""
    transporte = TransporteHttp()
    cliente = paso.destino_de(settings.ppi_provider_url, transporte)

    async def handler(campos: dict[str, Any]) -> None:
        await _handle(campos, cliente)

    async def periodico() -> None:
        await _periodico(cliente)

    try:
        await bus.consumir(
            group=GROUP,
            streams=STREAMS,
            handler=handler,
            on_dead_letter=_persist_dead_letter,
            status_name="intelligence-gateway",
            periodico=periodico,
            intervalo_periodico=PERIODO,
        )
    finally:
        await transporte.cerrar()
