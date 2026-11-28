# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El bucle de consumo del bus de eventos en PostgreSQL (ADR-049).

Un solo bucle para los cuatro grupos consumidores (workflow, notification,
signature, inteligencia), en vez de cuatro copias. Cada grupo aporta su
``handler`` —que recibe los campos planos y firmados, igual que antes— y su
``on_dead_letter``; el bucle pone lo común:

- recorre las entidades activas (los eventos viven en el schema de cada una),
  toma lo que el grupo no ha tomado (:func:`events.leer`), lo procesa y lo
  confirma — o deja el error y lo deja pendiente;
- cada ``RECLAIM_INTERVAL`` reclama lo atascado (:func:`events.reclaim_stale`);
- duerme hasta que un ``NOTIFY eventos`` lo despierta, o ``ESPERA`` segundos
  como respaldo: una notificación perdida mientras no había nadie escuchando
  solo retrasa, nunca pierde, porque la tabla es el registro.

Una sola conexión escucha por todo el proceso: con un pool de diez, una por
consumidor serían cuatro conexiones fijas sin hacer nada.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from orpycamcp_common import events
from orpycamcp_common.database import acquire_tenant, get_pool

from document.services.barrido import tenants_activos

logger = logging.getLogger(__name__)

#: Respaldo del despertador, en segundos.
ESPERA = 5.0
#: Cadencia de la reclamación. Contrato: MIN_IDLE (60) > RECLAIM_INTERVAL (30) > ESPERA (5).
RECLAIM_INTERVAL = 30.0
MIN_IDLE_S = 60
MAX_DELIVERIES = 5
#: Eventos que se toman por entidad y por vuelta.
LOTE = 10

Handler = Callable[[dict[str, str]], Awaitable[None]]


# ── El despertador compartido ─────────────────────────────────────────────────

_suscritos: set[asyncio.Event] = set()
_escucha: asyncio.Task | None = None


def _avisar(*_args: Any) -> None:
    for evento in _suscritos:
        evento.set()


async def _escuchar() -> None:
    """Mantiene una conexión con `LISTEN eventos`; si se cae, la reabre."""
    while True:
        try:
            async with get_pool().acquire() as conn:
                await conn.add_listener(events.CANAL, _avisar)
                try:
                    while not conn.is_closed():
                        await asyncio.sleep(ESPERA)
                finally:
                    await conn.remove_listener(events.CANAL, _avisar)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("bus: la escucha de NOTIFY se cayó; se reabre", exc_info=True)
        # Los consumidores siguen con el sondeo de respaldo mientras tanto.
        await asyncio.sleep(ESPERA)


def _suscribir() -> asyncio.Event:
    global _escucha
    evento = asyncio.Event()
    _suscritos.add(evento)
    if _escucha is None or _escucha.done():
        _escucha = asyncio.create_task(_escuchar(), name="bus:escucha")
    return evento


def _desuscribir(evento: asyncio.Event) -> None:
    global _escucha
    _suscritos.discard(evento)
    if not _suscritos and _escucha is not None:
        _escucha.cancel()
        _escucha = None


# ── El bucle ──────────────────────────────────────────────────────────────────


async def procesar_tenant(
    slug: str, *, group: str, streams: tuple[str, ...], handler: Handler
) -> int:
    """Una vuelta sobre una entidad. Devuelve cuántos eventos tomó.

    Cada evento se confirma por separado: un fallo deja ESE evento pendiente
    (lo reintentará la reclamación) y no frena a los demás.
    """
    async with acquire_tenant(slug) as conn:
        lote = await events.leer(conn, group, streams, limite=LOTE)
        for event_id, campos in lote:
            try:
                await handler(campos)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await events.record_failure(conn, group, event_id, exc)
                logger.warning(
                    "bus: %s falló con event_id=%s tenant=%s — queda pendiente",
                    group,
                    event_id,
                    slug,
                    exc_info=True,
                )
                continue
            await events.ack(conn, group, event_id)
    return len(lote)


async def reclamar_tenant(
    slug: str,
    *,
    group: str,
    handler: Handler,
    on_dead_letter: Any,
    status_name: str | None,
) -> events.ReclaimStats:
    async with acquire_tenant(slug) as conn:
        return await events.reclaim_stale(
            conn,
            group,
            handler=handler,
            min_idle_s=MIN_IDLE_S,
            max_deliveries=MAX_DELIVERIES,
            on_dead_letter=on_dead_letter,
            status_name=status_name,
        )


async def _por_tenant(accion: Callable[[str], Awaitable[Any]], group: str) -> list[Any]:
    resultados = []
    for slug in await tenants_activos(get_pool()):
        try:
            resultados.append(await accion(slug))
        except asyncio.CancelledError:
            raise
        except asyncpg.UndefinedTableError:
            # Entidad sin las migraciones del bus: no hay nada que consumir.
            logger.debug("bus: tenant=%s sin tablas de eventos", slug)
        except Exception:
            logger.warning("bus: %s omitió tenant=%s por un fallo", group, slug, exc_info=True)
    return resultados


async def consumir(
    *,
    group: str,
    streams: tuple[str, ...],
    handler: Handler,
    on_dead_letter: Any = None,
    status_name: str | None = None,
    periodico: Callable[[], Awaitable[None]] | None = None,
    intervalo_periodico: float = 86_400.0,
) -> None:
    """Bucle de un grupo consumidor. Se lanza bajo ``events.spawn_supervised``.

    ``periodico`` es el trabajo propio del grupo con su propia cadencia (p. ej.
    la retención diaria de sus dead-letters); un fallo ahí no para el consumo.
    """
    despertar = _suscribir()
    ultimo_reclamo = ultimo_periodico = time.monotonic()
    try:
        while True:
            despertar.clear()
            tomados = await _por_tenant(
                lambda slug: procesar_tenant(slug, group=group, streams=streams, handler=handler),
                group,
            )
            ahora = time.monotonic()
            if ahora - ultimo_reclamo >= RECLAIM_INTERVAL:
                await _por_tenant(
                    lambda slug: reclamar_tenant(
                        slug,
                        group=group,
                        handler=handler,
                        on_dead_letter=on_dead_letter,
                        status_name=status_name,
                    ),
                    group,
                )
                ultimo_reclamo = ahora
            if periodico is not None and ahora - ultimo_periodico >= intervalo_periodico:
                try:
                    await periodico()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("bus: el trabajo periódico de %s falló", group, exc_info=True)
                ultimo_periodico = ahora
            # Un lote lleno en alguna entidad: hay más, se sigue sin dormir.
            if any(n >= LOTE for n in tomados):
                continue
            try:
                await asyncio.wait_for(despertar.wait(), timeout=ESPERA)
            except TimeoutError:
                pass
    finally:
        _desuscribir(despertar)
