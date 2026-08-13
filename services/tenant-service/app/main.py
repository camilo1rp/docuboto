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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import request_log

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.catalogos import router as catalogos_router
from app.routers.config import router as config_router
from app.routers.dependencias import router as dependencias_router
from app.routers.health import router as health_router
from app.routers.pinar import router as pinar_router
from app.routers.tenants import router as tenants_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("tenant-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    yield
    logger.info("tenant-service shutting down")
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — Tenant Service",
    description=(
        "Manages institutions (tenants) and provisions isolated PostgreSQL schemas "
        "for multi-tenant document management."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
app.add_middleware(request_log.RequestLogMiddleware, servicio="tenant-service")

app.include_router(tenants_router)
app.include_router(config_router)
app.include_router(dependencias_router)
app.include_router(catalogos_router)
app.include_router(pinar_router)
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
