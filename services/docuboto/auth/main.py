# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
auth-service — FastAPI application factory.

Responsibilities:
- Issue JWT tokens via Keycloak ROPC grant (POST /api/v1/auth/token)
- Refresh and revoke tokens
- Validate Bearer tokens for the api-gateway (/api/v1/auth/validate)
- Audit every auth event to PostgreSQL
"""

import logging

from auth.routers.audit import router as audit_router
from auth.routers.auth import router as auth_router
from auth.routers.clearance import router as clearance_router
from auth.routers.context import router as context_router
from auth.routers.rbac import pick_router as rbac_pick_router
from auth.routers.rbac import router as rbac_router
from auth.routers.signing_key import admin_router as signing_key_admin_router
from auth.routers.signing_key import router as signing_key_router
from auth.routers.totp import admin_router as totp_admin_router
from auth.routers.totp import router as totp_router
from auth.routers.urd import router as urd_router

logger = logging.getLogger(__name__)


DOMAIN_ROUTERS = (
    auth_router,
    rbac_pick_router,  # D-10: antes de rbac_router (ruta /users/pickable)
    rbac_router,
    urd_router,
    context_router,
    clearance_router,
    totp_router,
    totp_admin_router,
    signing_key_router,
    signing_key_admin_router,
    audit_router,
)


