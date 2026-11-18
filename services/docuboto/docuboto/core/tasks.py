# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El grupo de tareas de fondo del proceso (ADR-047: sustituye al `worker`).

Todo bucle arranca aquí y se cancela aquí. Los que pueden caerse por una
excepción van supervisados (`events.spawn_supervised`, que los relanza); los que
ya tragan sus propios errores van tal cual.

El trabajo pesado —veraPDF, la migración de formato con Ghostscript, la
renovación LTA— comparte `heavy_slot()`: un semáforo de `HEAVY_JOBS_CONCURRENCY`
plazas (1 por defecto). Antes vivía en otro contenedor para no quitarle CPU a
las peticiones; aquí el semáforo es lo que lo acota.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable

from orpycamcp_common import events

logger = logging.getLogger(__name__)

_heavy: asyncio.Semaphore | None = None


def heavy_jobs_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("HEAVY_JOBS_CONCURRENCY", "1")))
    except ValueError:
        return 1


@contextlib.asynccontextmanager
async def heavy_slot() -> AsyncIterator[None]:
    """Una plaza de trabajo pesado. Se crea en el bucle que la pide."""
    global _heavy
    if _heavy is None:
        _heavy = asyncio.Semaphore(heavy_jobs_concurrency())
    async with _heavy:
        yield


class BackgroundTasks:
    """Los bucles del proceso: se arrancan con `start` y se paran juntos."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def start(
        self,
        name: str,
        factory: Callable[[], Awaitable[None]],
        *,
        supervised: bool = False,
    ) -> None:
        if supervised:
            self._tasks.append(events.spawn_supervised(factory, name=name, logger=logger))
        else:
            self._tasks.append(asyncio.create_task(factory(), name=name))  # type: ignore[arg-type]

    @property
    def names(self) -> list[str]:
        return [t.get_name() for t in self._tasks]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
