# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Factory del migrador de formato activo (RF-PRE-04).

**Devuelve `None` cuando no hay convertidor, y ese `None` es la parte
importante.** La factory gemela del validador PDF/A degrada a un stub honesto,
porque «no evaluado» es un resultado real. Aquí no existe el equivalente: un
stub tendría que **producir un archivo**, y el único que puede producir sin la
herramienta es uno inventado. Así que la ausencia se propaga hasta el endpoint,
que responde `503` diciendo qué falta — nunca una copia de preservación que
nadie generó.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

from app.services.migracion.subprocess_ghostscript import SubprocessGhostscriptMigrator
from app.services.migracion.types import FormatMigrator

logger = logging.getLogger(__name__)


class _SettingsLike(Protocol):
    format_migrator: str


def _resolver_binario(settings: _SettingsLike) -> str | None:
    """Igual que el validador: ruta ABSOLUTA a un ejecutable existente, o nada.
    `getattr` con default porque los `settings`-like de los tests y los
    despliegues previos a este incremento no declaran el atributo — eso también
    debe resolver a «no hay convertidor», no a `AttributeError`."""
    binario = getattr(settings, "ghostscript_binary", None)
    if not binario:
        return None
    if not os.path.isabs(binario):
        logger.warning("GHOSTSCRIPT_BINARY=%r no es una ruta absoluta -- ignorado.", binario)
        return None
    if not (os.path.isfile(binario) and os.access(binario, os.X_OK)):
        logger.warning("GHOSTSCRIPT_BINARY=%r no existe o no es ejecutable -- ignorado.", binario)
        return None
    return binario


def get_format_migrator(settings: _SettingsLike) -> FormatMigrator | None:
    """El migrador a usar, o `None` si este despliegue no tiene con qué migrar."""
    nombre = getattr(settings, "format_migrator", "none")

    if nombre == "ghostscript":
        binario = _resolver_binario(settings)
        if binario is not None:
            return SubprocessGhostscriptMigrator(
                binary=binario,
                timeout_seconds=getattr(settings, "ghostscript_timeout_seconds", 120),
                max_concurrency=getattr(settings, "ghostscript_max_concurrency", 1),
            )
        logger.warning(
            "FORMAT_MIGRATOR=ghostscript solicitado, pero GHOSTSCRIPT_BINARY no apunta "
            "a un ejecutable real -- NO hay migración de formato en este despliegue "
            "(no se degrada a un stub: un migrador falso tendría que inventarse el archivo)."
        )
        return None

    if nombre != "none":
        logger.warning("FORMAT_MIGRATOR=%r desconocido -- sin migración de formato.", nombre)
    return None
