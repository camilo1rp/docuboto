# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
storage-service — FastAPI application factory.

Responsibilities:
- File upload/download for radicado anexos (attachments)
- SHA-256 deduplication per tenant to avoid redundant MinIO storage
- Presigned URL generation — the service never proxies file bytes
- Tenant-isolated storage: PostgreSQL schema per tenant + MinIO bucket per tenant
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.core.minio_client import init_minio
from app.routers.health import router as health_router
from app.routers.preservacion import router as preservacion_router
from app.routers.storage import router as storage_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("storage-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    init_minio(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_use_ssl,
    )
    yield
    logger.info("storage-service shutting down")
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — Storage Service",
    description=(
        "Manages file storage for radicado anexos. Uploads to MinIO with "
        "SHA-256 deduplication per tenant. Issues presigned URLs for downloads."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(storage_router)
app.include_router(preservacion_router)
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
