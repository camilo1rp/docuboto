# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.core.redis_client import close_redis, init_redis
from app.routers.admin_deadletter import router as admin_dlq_router
from app.routers.cadena import router as cadena_router
from app.routers.health import router as health_router
from app.routers.signature import router as signature_router
from app.workers import document_consumer

logging.basicConfig(level=settings.log_level.upper())

logger = logging.getLogger("signature.startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("signature-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    await init_redis(settings.redis_url)
    # Re-sync firma_solicitud.nivel_seguridad on reclassification events (RF-SEG-08).
    consumer_task = asyncio.create_task(document_consumer.run())
    yield
    logger.info("signature-service shutting down")
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await close_pool()
    await close_redis()


app = FastAPI(
    title="OrpycaMCP — Signature Service",
    description=(
        "Firma electrónica (E06, ADR-016): sign-xml + verify. Proveedor pluggable "
        "(nativo hash+identidad por defecto; PKI XAdES/PAdES/TSA como swap)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(signature_router)
app.include_router(cadena_router)
app.include_router(admin_dlq_router)
app.include_router(health_router)
