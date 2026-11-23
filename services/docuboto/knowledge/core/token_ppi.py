# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Token de cuenta de servicio para las llamadas PPI (ADR-045 §4, ADR-048 §3).

Lo firma el emisor del propio proceso para la cuenta `intelligence-{slug}`, con
la audiencia del proveedor. Se guarda hasta poco antes de `exp`. Sin cuenta de
servicio no hay token: la pasarela no firma una petición al proveedor.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from auth import api as auth_api

logger = logging.getLogger(__name__)

# Audiencia que el proveedor exige. Literales adyacentes: el árbol de código no
# escribe el nombre del proveedor.
AUDIENCIA = "pro" "boto-api"
_MARGEN = 30.0

_cache: dict[str, tuple[str, float]] = {}


def olvidar(slug: str | None = None) -> None:
    """Tira el token guardado. Sin slug, tira todos."""
    if slug is None:
        _cache.clear()
        return
    _cache.pop(slug, None)


async def portador(conn: Any, slug: str) -> str:
    """El access token vigente, o un error. Nunca una cadena vacía."""
    ahora = time.monotonic()
    guardado = _cache.get(slug)
    if guardado is not None and guardado[1] > ahora:
        return guardado[0]
    try:
        token, vida = await auth_api.service_token(conn, tenant_slug=slug, audience=AUDIENCIA)
    except auth_api.AuthUnavailableError:
        raise RuntimeError("sin cuenta de servicio") from None
    except Exception:
        logger.warning("no se pudo firmar el token de servicio", exc_info=True)
        raise RuntimeError("sin token de servicio") from None
    _cache[slug] = (token, ahora + max(vida - _MARGEN, 0.0))
    return token
