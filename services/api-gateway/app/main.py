# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.client_ip import exigir_proxies_sin_gateway, gateway_de_la_ruta_por_defecto
from app.core.config import settings
from app.middleware.auth import JWTValidationMiddleware
from app.middleware.path_guard import PathNormalizationMiddleware
from app.middleware.rate_limit import RateLimitMiddleware, RateLimitPorUsuarioMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.routers import health, openapi, proxy

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


def _exigir_proxies_de_produccion() -> None:
    """Producción: `TRUSTED_PROXIES` no puede contener la puerta de enlace de la red (ARC-B4)."""
    if settings.app_env.strip().lower() != "production":
        return
    gateway = settings.gateway_red_docker_gateway.strip() or gateway_de_la_ruta_por_defecto()
    if gateway is None:
        logger.warning(
            "APP_ENV=production pero no se pudo determinar la puerta de enlace de la red: "
            "declare GATEWAY_RED_DOCKER_GATEWAY para que se compruebe TRUSTED_PROXIES"
        )
        return
    exigir_proxies_sin_gateway(settings.trusted_proxies, gateway)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _exigir_proxies_de_produccion()
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
    app.state.knowledge_service_url = settings.knowledge_service_url
    app.state.rate_limit_per_ip = settings.rate_limit_per_ip
    app.state.rate_limit_global = settings.rate_limit_global
    app.state.rate_limit_per_usuario = settings.rate_limit_per_usuario
    app.state.rate_limit_per_tenant = settings.rate_limit_per_tenant
    app.state.rate_limit_login_per_ip = settings.rate_limit_login_per_ip
    app.state.max_request_body_bytes = settings.max_request_body_bytes
    app.state.max_public_body_bytes = settings.max_public_body_bytes
    app.state.max_upload_body_bytes = settings.max_upload_body_bytes
    app.state.openapi_publico = settings.openapi_publico
    app.state.trusted_proxies = settings.trusted_proxies
    app.state.rate_limit_pqrs_per_ip = settings.rate_limit_pqrs_per_ip
    app.state.rate_limit_cosecha_per_ip = settings.rate_limit_cosecha_per_ip
    app.state.auth_cache_ttl = settings.auth_cache_ttl
    app.state.auth_cache_mac_secret = settings.auth_cache_mac_secret

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

# Starlette inserts at index 0 then reverses to build the stack.
# To get PathNormalization → RateLimit → JWT on the request path, se añaden en el
# orden INVERSO al que corren. El guard de ruta va antes de la autenticación y
# del ruteo a propósito: son las dos decisiones que un salto `..` engaña (ver
# middleware/path_guard.py).
# Registrado ANTES de la validación del JWT, y por eso se ejecuta DESPUÉS de
# ella: Starlette aplica los middlewares en orden inverso al de registro, así
# que el primero registrado es el más interno. Necesita la identidad ya
# resuelta y firmada para contar por persona en vez de por dirección de red.
app.add_middleware(RateLimitPorUsuarioMiddleware)
app.add_middleware(JWTValidationMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(PathNormalizationMiddleware)

_cors_origins = settings.cors_origins_list
if _cors_origins:
    # `*` y credenciales son incompatibles por seguridad, no por capricho del
    # navegador: con credenciales Starlette refleja el `Origin` recibido, que
    # equivale a autorizarlos todos. Si el operador pide comodín, se le da
    # comodín SIN credenciales.
    _comodin = "*" in _cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if _comodin else _cors_origins,
        allow_credentials=not _comodin,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info(
        "CORS habilitado para %s (credenciales: %s)",
        "cualquier origen" if _comodin else ", ".join(_cors_origins),
        not _comodin,
    )
else:
    logger.info("CORS deshabilitado (CORS_ALLOW_ORIGINS vacío): el frontend habla por SSR")

# El MÁS externo: el identificador de petición tiene que existir antes de que
# nada —incluido el rechazo por ruta o por rate limit— tenga algo que registrar.
app.add_middleware(RequestContextMiddleware)

app.include_router(health.router)
app.include_router(openapi.router)
app.include_router(proxy.router)

# `X-Request-Id` y `X-Gateway-Version` los pone ahora RequestContextMiddleware,
# que corre ANTES del proxy: el identificador viaja a los servicios en vez de
# inventarse al volver.
