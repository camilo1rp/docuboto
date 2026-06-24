# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
archive-service — FastAPI application factory.

Responsibilities:
- Expediente management (grouping of related radicados by matter/case)
- TRD (Tabla de Retención Documental) catalogue — retention schedules and dispositions
- Tenant-isolated storage: PostgreSQL schema per tenant (SET search_path)
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.batch import router as batch_router
from app.routers.expedientes import router as expedientes_router
from app.routers.fisico import router as fisico_router
from app.routers.health import router as health_router
from app.routers.indice import router as indice_router
from app.routers.metadata import router as metadata_router
from app.routers.tipos_documentales import router as tipos_documentales_router
from app.routers.transferencias import router as transferencias_router
from app.routers.trd import router as trd_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("archive-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    yield
    logger.info("archive-service shutting down")
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — Archive Service",
    description=(
        "Manages expedientes (logical document groupings) and the Tabla de Retención "
        "Documental (TRD). Provides complete lifecycle tracking from creation to "
        "final disposition in accordance with Ley 594/2000."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(batch_router)
app.include_router(expedientes_router)
app.include_router(indice_router)
app.include_router(fisico_router)
app.include_router(transferencias_router)
app.include_router(metadata_router)
app.include_router(tipos_documentales_router)
app.include_router(trd_router)
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
