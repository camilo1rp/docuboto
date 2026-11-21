# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
tenant-service — FastAPI application factory.

Responsibilities:
- Manage tenant (institution) registration and lifecycle
- Provision per-tenant PostgreSQL schemas: tenant_{slug}
- Expose tenant lookup endpoints for other services to validate JWT claims
"""

import logging

from tenant.routers.catalogos import router as catalogos_router
from tenant.routers.config import router as config_router
from tenant.routers.dependencias import router as dependencias_router
from tenant.routers.pinar import router as pinar_router

logger = logging.getLogger(__name__)


DOMAIN_ROUTERS = (
    config_router,
    dependencias_router,
    catalogos_router,
    pinar_router,
)


