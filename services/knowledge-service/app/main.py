# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from orpycamcp_common import identidad, request_log

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.health import router as health_router
from app.routers.knowledge import router as knowledge_router

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("knowledge-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    yield
    logger.info("knowledge-service shutting down")
    await close_pool()


app = FastAPI(
    title="OrpycaMCP — Knowledge Service",
    description=(
        "Pasarela de conocimiento (ADR-045, ADR-046). Sin proveedor (`local`), buscar y "
        "antecedentes responden con la búsqueda de texto completo de document-service, "
        "consultada como el usuario, y no hay generación. No llama a ningún modelo."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
# La identidad que inyecta el gateway viene FIRMADA: sin esto, cualquiera
# que alcance el puerto de este servicio dentro de la red podía hablarle
# diciendo ser quien quisiera y de la entidad que quisiera. Va el primero
# —o sea el más externo— porque es la decisión que precede a todas.
app.add_middleware(identidad.IdentidadFirmadaMiddleware)
app.add_middleware(request_log.RequestLogMiddleware, servicio="knowledge-service")

app.include_router(knowledge_router)
app.include_router(health_router)
