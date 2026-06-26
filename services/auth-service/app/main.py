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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.auth import set_auth_service
from app.routers.clearance import router as clearance_router
from app.routers.context import router as context_router
from app.routers.health import router as health_router
from app.routers.rbac import router as rbac_router
from app.routers.urd import router as urd_router
from app.services.auth_service import AuthService

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("auth-service starting up")
    # Fail-fast visible del contrato de issuer: deja en el log el `iss` esperado
    # y avisa si la configuración rechazaría todos los tokens (ver config.py).
    logger.info("JWT issuer esperado (iss): %s", settings.expected_issuer)
    for warning in settings.issuer_config_warnings():
        logger.warning(warning)
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    service = AuthService()
    set_auth_service(service)
    yield
    logger.info("auth-service shutting down")
    await service.close()
    await close_pool()


app = FastAPI(
    title="OrpycaMCP — Auth Service",
    description=(
        "Handles authentication against Keycloak and JWT validation "
        "for all OrpycaMCP microservices."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(auth_router)
app.include_router(rbac_router)
app.include_router(urd_router)
app.include_router(context_router)
app.include_router(clearance_router)
app.include_router(audit_router)
app.include_router(health_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "An unexpected error occurred",
            "status": 500,
        },
    )
