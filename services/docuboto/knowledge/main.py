# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import logging

from knowledge.routers.ingesta import router as ingesta_router
from knowledge.routers.knowledge import router as knowledge_router
from knowledge.routers.retorno import router as retorno_router

logger = logging.getLogger(__name__)


DOMAIN_ROUTERS = (knowledge_router, ingesta_router, retorno_router)
