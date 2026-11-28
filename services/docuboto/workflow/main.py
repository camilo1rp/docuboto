# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
workflow-service — FastAPI application factory.

Responsibilities:
- Distribution and routing of radicados between dependencias (departments)
- Full audit trail of every step in a document's lifecycle
- Domain events on every state change (PostgreSQL bus, ADR-049)
- Tenant-isolated storage: PostgreSQL schema per tenant (SET search_path)
"""

import logging

from workflow.routers.admin_deadletter import router as admin_dlq_router
from workflow.routers.rules import router as rules_router
from workflow.routers.visto_bueno import router as visto_bueno_router
from workflow.routers.workflow import router as workflow_router

logger = logging.getLogger(__name__)


DOMAIN_ROUTERS = (workflow_router, rules_router, visto_bueno_router, admin_dlq_router)


