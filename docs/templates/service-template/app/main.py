# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
__SERVICE_NAME__-service — FastAPI application factory.

Reemplaza esta descripción por las responsabilidades del servicio.
Convenciones del proyecto (ver CLAUDE.md y docs/es/adr/):
- asyncpg + SQL crudo (sin SQLAlchemy/ORM) — ADR-003.
- Multi-tenant por schema tenant_{slug} — ADR-002.
- Eventos por Redis Streams orpycamcp.__SERVICE_NAME__.events — ver contrato de eventos.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.health import router as health_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("__SERVICE_NAME__-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    yield
    logger.info("__SERVICE_NAME__-service shutting down")
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — __SERVICE_TITLE__ Service",
    description="Reemplaza por la descripción del servicio.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Registra aquí los routers del servicio (uno por recurso).
app.include_router(health_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "Ocurrió un error inesperado",
            "status": 500,
        },
    )
