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
- Redis Stream events on every state change
- Tenant-isolated storage: PostgreSQL schema per tenant (SET search_path)
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import deadletter, events, identidad, request_log

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.core.redis_client import close_redis, init_redis
from app.routers.admin_deadletter import router as admin_dlq_router
from app.routers.health import router as health_router
from app.routers.metrics import router as metrics_router
from app.routers.rules import router as rules_router
from app.routers.visto_bueno import router as visto_bueno_router
from app.routers.workflow import router as workflow_router
from app.workers import document_consumer

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("workflow-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    await init_redis(settings.redis_url)
    # Distribución automática (RF-FLU-04): consume orpycamcp.document.events.
    consumer_task = events.spawn_supervised(
        document_consumer.run, name="workflow", logger=logger
    )
    yield
    logger.info("workflow-service shutting down")
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await close_pool()
    await close_redis()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — Workflow Service",
    description=(
        "Manages distribution and routing of radicados between dependencias. "
        "Records every transfer with full traceability and publishes events to Redis Streams."
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
app.add_middleware(request_log.RequestLogMiddleware, servicio="workflow-service")

app.include_router(workflow_router)
app.include_router(rules_router)
app.include_router(visto_bueno_router)
app.include_router(admin_dlq_router)
deadletter.instalar_manejador_radicados_ausente(app)
app.include_router(health_router)
app.include_router(metrics_router)  # C18: GET /metrics, NUNCA vía gateway (404 con cabeceras de proxy)


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
