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
from orpycamcp_common import events

from app.core.config import settings
from app.core.database import close_pool, init_pool
from app.core.redis_client import close_redis, init_redis
from app.routers import admin_deadletter, health, notifications, webhooks
from app.workers import stream_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_pool(settings.database_url_asyncpg)
    await init_redis(settings.redis_url)
    worker_task = events.spawn_supervised(
        stream_worker.run, name="notification", logger=logger
    )
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await close_redis()
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP Notification Service",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(notifications.router)
app.include_router(webhooks.router)
app.include_router(admin_deadletter.router)
