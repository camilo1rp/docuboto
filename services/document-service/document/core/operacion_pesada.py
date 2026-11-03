# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Una importación o exportación pesada a la vez por proceso (E11).

Import y export arman el paquete ZIP ENTERO en memoria. Sin un tope de
concurrencia, varios `POST /import` de ~512 MiB bufferizados a la vez agotan la
RAM del worker, que atiende a todos los tenants. El cupo es de proceso (no por
tenant): lo que se protege es la memoria del worker compartido.

Si el cupo está ocupado NO se encola —encolar es retener otro buffer—: se responde
429 y el cliente reintenta. Job asíncrono con cola y streaming siguen en roadmap.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_CUPO = asyncio.Semaphore(1)


class OperacionPesadaOcupadaError(Exception):
    """Ya hay una importación/exportación en curso en este proceso → 429."""


@asynccontextmanager
async def una_operacion_pesada() -> AsyncIterator[None]:
    # `locked()` + `async with` sin un `await` entre medias: en asyncio no puede
    # colarse otra tarea entre la comprobación y la toma del cupo.
    if _CUPO.locked():
        raise OperacionPesadaOcupadaError
    async with _CUPO:
        yield
