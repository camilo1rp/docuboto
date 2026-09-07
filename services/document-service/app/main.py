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

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import identidad, request_log
from orpycamcp_common import secrets as common_secrets

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.anulacion import cola_router as anulacion_cola_router
from app.routers.anulacion import router as anulacion_router
from app.routers.batch import router as batch_router
from app.routers.borradores import router as borradores_router
from app.routers.cmis import router as cmis_router
from app.routers.documents import router as documents_router
from app.routers.export import router as export_router
from app.routers.health import router as health_router
from app.routers.import_ import router as import_router
from app.routers.ingest import apps_router as email_apps_router
from app.routers.ingest import bandeja_router as email_bandeja_router
from app.routers.ingest import cuentas_router as email_cuentas_router
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
from app.routers.terceros import router as terceros_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # El token servicio-a-servicio no puede traer el marcador publicado.
    #
    # El guardián de secretos existía y cubría tres servicios de once, así que
    # los tokens internos —que son los que abren los endpoints sellados— podían
    # quedarse con el valor de ejemplo del repositorio sin que nada lo dijera.
    # Una lista de admisión que crece aparte del sistema que vigila acaba
    # cubriendo lo que alguien recordó, no lo que hace falta.
    common_secrets.require_secrets(
        {"DOCUMENT_INTERNAL_TOKEN": settings.internal_service_token}, servicio="document-service"
    )

    logger.info("document-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    # Cliente HTTP compartido hacia tenant-service (cálculo de días hábiles).
    app.state.http_client = httpx.AsyncClient(base_url=settings.tenant_service_url, timeout=5.0)
    # Redis para publicar eventos de dominio (orpycamcp.document.events, ADR-010).
    app.state.redis = redis.from_url(settings.redis_url)
    # Sondeo programado de buzones (E19). Opt-in y con entidad declarada: sin
    # ambas cosas no arranca, y se dice en el registro para que la ausencia no
    # se confunda con un fallo silencioso.
    app.state.tarea_sondeo = None
    if settings.email_sondeo_enabled:
        if settings.email_sondeo_tenant_slug:
            from app.services.email_sondeo import bucle_de_sondeo

            app.state.tarea_sondeo = asyncio.create_task(
                bucle_de_sondeo(app.state.db_pool, settings.email_sondeo_tenant_slug)
            )
        else:
            logger.warning(
                "EMAIL_SONDEO_ENABLED está activo pero falta EMAIL_SONDEO_TENANT_SLUG: "
                "el sondeo de correo NO arranca (un sondeo sin entidad declarada "
                "radicaría en el archivo equivocado)"
            )

    yield
    logger.info("document-service shutting down")
    if app.state.tarea_sondeo is not None:
        app.state.tarea_sondeo.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.tarea_sondeo
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
# La identidad que inyecta el gateway viene FIRMADA: sin esto, cualquiera
# que alcance el puerto de este servicio dentro de la red podía hablarle
# diciendo ser quien quisiera y de la entidad que quisiera. Va el primero
# —o sea el más externo— porque es la decisión que precede a todas.
app.add_middleware(identidad.IdentidadFirmadaMiddleware)
app.add_middleware(request_log.RequestLogMiddleware, servicio="document-service")

app.include_router(batch_router)
app.include_router(documents_router)
app.include_router(metadata_router)
app.include_router(metadata_elements_router)
app.include_router(search_router)
app.include_router(terceros_router)
app.include_router(reports_router)
app.include_router(public_router)
app.include_router(email_cuentas_router)
app.include_router(email_apps_router)
app.include_router(email_bandeja_router)
app.include_router(signatures_router)
app.include_router(postal_router)
# Interoperabilidad pública ANÓNIMA: el router se monta siempre, pero sus rutas
# responden 404 mientras la entidad no la habilite — ver core/public_interop.py.
app.include_router(oai_router)
app.include_router(cmis_router)

app.include_router(export_router)
app.include_router(import_router)
app.include_router(anulacion_router)
app.include_router(anulacion_cola_router)
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
            "detail": "Ocurrió un error inesperado",
            "status": 500,
        },
    )
