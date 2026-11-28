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

from signature.routers.admin_sellos import router as admin_sellos_router
from signature.routers.cadena import router as cadena_router
from signature.routers.public import router as public_router
from signature.routers.signature import router as signature_router

logger = logging.getLogger("signature.startup")


DOMAIN_ROUTERS = (
    signature_router,
    cadena_router,
    admin_sellos_router,
    public_router,
)
