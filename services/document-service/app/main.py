# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
document-service — FastAPI application factory.

Responsibilities:
- Radicación of official documents (Entrada, Salida, Interno)
- Atomic tracking number assignment per tenant, year, and document type
- Tenant-isolated storage via PostgreSQL schema switching (tenant_{slug})
- Anexo references — file UUIDs from storage-service only (no direct MinIO)
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import request_log

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.anulacion import router as anulacion_router
from app.routers.batch import router as batch_router
from app.routers.borradores import router as borradores_router
from app.routers.cmis import router as cmis_router
from app.routers.documents import router as documents_router
from app.routers.export import router as export_router
from app.routers.health import router as health_router
from app.routers.import_ import router as import_router
from app.routers.ingest import router as ingest_router
from app.routers.internal import router as internal_router
from app.routers.metadata import router as metadata_router
from app.routers.metadata_elements import router as metadata_elements_router
from app.routers.oai import router as oai_router
from app.routers.postal import router as postal_router
from app.routers.public import router as public_router
from app.routers.reports import router as reports_router
from app.routers.respuesta import router as respuesta_router
from app.routers.search import router as search_router
from app.routers.signatures import router as signatures_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("document-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    # Cliente HTTP compartido hacia tenant-service (cálculo de días hábiles).
    app.state.http_client = httpx.AsyncClient(base_url=settings.tenant_service_url, timeout=5.0)
    # Redis para publicar eventos de dominio (orpycamcp.document.events, ADR-010).
    app.state.redis = redis.from_url(settings.redis_url)
    # Cliente IMAP para la ingesta de correo (E19); None si IMAP no está configurado.
    if settings.imap_host:
        from app.clients.imap_client import ImaplibClient

        app.state.imap_client = ImaplibClient(
            settings.imap_host,
            settings.imap_port,
            settings.imap_user,
            settings.imap_password,
            use_ssl=settings.imap_use_ssl,
            mailbox=settings.imap_mailbox,
        )
    else:
        app.state.imap_client = None
    yield
    logger.info("document-service shutting down")
    await app.state.http_client.aclose()
    await app.state.redis.aclose()
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — Document Service",
    description=(
        "Manages radicación of official documents (Entrada, Salida, Interno) "
        "with atomic tracking number assignment. Implements Ley 594/2000 and "
        "Acuerdo AGN 060/2001 requirements for Colombian public institutions."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
app.add_middleware(request_log.RequestLogMiddleware, servicio="document-service")

app.include_router(batch_router)
app.include_router(documents_router)
app.include_router(metadata_router)
app.include_router(metadata_elements_router)
app.include_router(search_router)
app.include_router(reports_router)
app.include_router(public_router)
app.include_router(ingest_router)
app.include_router(signatures_router)
app.include_router(postal_router)
# Interoperabilidad pública ANÓNIMA: el router se monta siempre, pero sus rutas
# responden 404 mientras la entidad no la habilite — ver core/public_interop.py.
app.include_router(oai_router)
app.include_router(cmis_router)

app.include_router(export_router)
app.include_router(import_router)
app.include_router(anulacion_router)
app.include_router(respuesta_router)
app.include_router(borradores_router)
app.include_router(internal_router)
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
