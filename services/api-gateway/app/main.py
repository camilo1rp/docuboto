# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.middleware.auth import JWTValidationMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.routers import health, openapi, proxy

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.http_client = httpx.AsyncClient(timeout=settings.proxy_timeout)
    app.state.auth_service_url = settings.auth_service_url
    app.state.tenant_service_url = settings.tenant_service_url
    app.state.document_service_url = settings.document_service_url
    app.state.archive_service_url = settings.archive_service_url
    app.state.storage_service_url = settings.storage_service_url
    app.state.workflow_service_url = settings.workflow_service_url
    app.state.notification_service_url = settings.notification_service_url
    app.state.signature_service_url = settings.signature_service_url
    app.state.mcp_service_url = settings.mcp_service_url
    app.state.assistant_proxy_timeout = settings.assistant_proxy_timeout
    app.state.rate_limit_per_ip = settings.rate_limit_per_ip
    app.state.rate_limit_global = settings.rate_limit_global

    try:
        redis_client = await aioredis.from_url(settings.redis_url)
        await redis_client.ping()
        app.state.redis = redis_client
        logger.info("Redis connected: %s", settings.redis_url)
    except Exception as exc:
        logger.warning("Redis not available — rate limiting disabled: %s", exc)
        app.state.redis = None

    yield

    await app.state.http_client.aclose()
    if app.state.redis is not None:
        await app.state.redis.aclose()


app = FastAPI(
    title="OrpycaMCP API Gateway",
    version="1.0.0",
    description="Single entry point for all OrpycaMCP microservices",
    lifespan=lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Starlette inserts at index 0 then reverses to build the stack.
# To get RateLimit → JWT on the request path, JWT must be added first
# (ends up at a higher index, wrapped inside RateLimit).
app.add_middleware(JWTValidationMiddleware)
app.add_middleware(RateLimitMiddleware)

app.include_router(health.router)
app.include_router(openapi.router)
app.include_router(proxy.router)


@app.middleware("http")
async def add_gateway_headers(request: Request, call_next: Any) -> Response:
    response: Response = await call_next(request)
    response.headers["X-Request-Id"] = str(uuid.uuid4())
    response.headers["X-Gateway-Version"] = "1.0.0"
    return response
