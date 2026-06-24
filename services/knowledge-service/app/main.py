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

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.health import router as health_router
from app.routers.knowledge import router as knowledge_router

logging.basicConfig(level=settings.log_level.upper())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    yield
    await close_pool()


app = FastAPI(
    title="OrpycaMCP — Knowledge Service",
    description=(
        "Capa de conocimiento ADVISORIA y derivada (E21, ADR-006): recuperación "
        "semántica (pgvector) con pre-filtrado ACL. Opt-in por tenant; no es fuente "
        "de verdad. Embeddings con proveedor pluggable (stub local por defecto)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(knowledge_router)
app.include_router(health_router)
