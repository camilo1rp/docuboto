# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente Redis del proceso, para lo que document hace sin `Request`.

Lo fija la raíz de composición (`docuboto.core.pools.bind_redis`). Las rutas
siguen leyendo `app.state.redis`; esto es para `document.api`, que no tiene
petición propia.
"""

from __future__ import annotations

from typing import Any

_redis: Any | None = None


def get_redis_or_none() -> Any | None:
    """El cliente, o `None` si el proceso no lo fijó (pruebas, imagen partida)."""
    return _redis
