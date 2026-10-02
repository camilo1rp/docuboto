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

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from orpycamcp_common import events, identidad, request_log
from orpycamcp_common import secrets as common_secrets

from app.core import webhook_secrets
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
        {
            "NOTIFICATION_INTERNAL_TOKEN": settings.internal_service_token,
            # KEK que cifra en reposo el secreto HMAC de los webhooks. Sin ella no
            # se pueden firmar entregas ni guardar un secreto nuevo.
            "WEBHOOK_SECRET_KEK": settings.webhook_secret_kek,
        },
        servicio="notification-service",
    )
    # Y además tiene que ser UTILIZABLE: base64 de 32 bytes y la versión activa
    # presente en el llavero. Fail-closed: mejor no arrancar que fallar por evento.
    webhook_secrets.validate_kek_at_startup()

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



@app.exception_handler(RequestValidationError)
async def _validacion_sin_eco(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 sin el valor recibido.

    El manejador por defecto copia `input` (y `ctx`) de cada error de pydantic: con
    `secret` más largo de 255 caracteres devolvía el SECRETO DEL WEBHOOK en claro en
    la respuesta. Se conserva dónde (`loc`), por qué (`msg`) y la clase (`type`), que
    es lo que el cliente necesita para corregir, y nunca lo que escribió."""
    detalle = [
        {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detalle})


app.include_router(health.router)
app.include_router(notifications.router)
app.include_router(webhooks.router)
app.include_router(admin_deadletter.router)
