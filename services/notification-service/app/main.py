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
from orpycamcp_common import events, identidad, request_log
from orpycamcp_common import secrets as common_secrets

from app.core.config import settings
from app.core.database import close_pool, init_pool
from app.core.redis_client import close_redis, init_redis
from app.routers import admin_deadletter, health, notifications, webhooks
from app.workers import stream_worker

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
        {"NOTIFICATION_INTERNAL_TOKEN": settings.internal_service_token}, servicio="notification-service"
    )

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

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
# La identidad que inyecta el gateway viene FIRMADA: sin esto, cualquiera
# que alcance el puerto de este servicio dentro de la red podía hablarle
# diciendo ser quien quisiera y de la entidad que quisiera. Va el primero
# —o sea el más externo— porque es la decisión que precede a todas.
app.add_middleware(identidad.IdentidadFirmadaMiddleware)
app.add_middleware(request_log.RequestLogMiddleware, servicio="notification-service")

app.include_router(health.router)
app.include_router(notifications.router)
app.include_router(webhooks.router)
app.include_router(admin_deadletter.router)
