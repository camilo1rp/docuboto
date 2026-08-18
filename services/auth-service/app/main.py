# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
auth-service — FastAPI application factory.

Responsibilities:
- Issue JWT tokens via Keycloak ROPC grant (POST /api/v1/auth/token)
- Refresh and revoke tokens
- Validate Bearer tokens for the api-gateway (/api/v1/auth/validate)
- Audit every auth event to PostgreSQL
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import request_log

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.auth import set_auth_service
from app.routers.clearance import router as clearance_router
from app.routers.context import router as context_router
from app.routers.health import router as health_router
from app.routers.rbac import pick_router as rbac_pick_router
from app.routers.rbac import router as rbac_router
from app.routers.signing_key import admin_router as signing_key_admin_router
from app.routers.signing_key import internal_router as signing_key_internal_router
from app.routers.signing_key import router as signing_key_router
from app.routers.totp import internal_router as totp_internal_router
from app.routers.totp import router as totp_router
from app.routers.urd import router as urd_router
from app.services.auth_service import AuthService

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("auth-service starting up")
    # Fail-fast visible del contrato de issuer: deja en el log el `iss` esperado
    # y avisa si la configuración rechazaría todos los tokens (ver config.py).
    logger.info("JWT issuer esperado (iss): %s", settings.expected_issuer)
    for warning in settings.issuer_config_warnings():
        logger.warning(warning)
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    service = AuthService()
    set_auth_service(service)
    if not settings.totp_encryption_key:
        logger.warning(
            "totp_encryption_key sin configurar — /me/totp/enroll fallará (500) "
            "hasta que se defina (AES-256, 32 bytes en base64)"
        )
    if not settings.signing_key_encryption_key:
        logger.warning(
            "signing_key_encryption_key sin configurar — /me/signing-key/enroll "
            "fallará (500) hasta que se defina (AES-256, 32 bytes en base64)"
        )
    if not settings.signing_key_ca_users_cert_path:
        logger.warning(
            "signing_key_ca_users_cert_path sin configurar — "
            "/internal/signing-key/activate fallará (503) hasta que se monte "
            "el certificado PÚBLICO de la sub-CA de usuarios (ca-users.crt)"
        )
    yield
    logger.info("auth-service shutting down")
    await service.close()
    await close_pool()


app = FastAPI(
    title="OrpycaMCP — Auth Service",
    description=(
        "Handles authentication against Keycloak and JWT validation "
        "for all OrpycaMCP microservices."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
app.add_middleware(request_log.RequestLogMiddleware, servicio="auth-service")

app.include_router(auth_router)
app.include_router(rbac_pick_router)  # D-10: antes de rbac_router (ruta /users/pickable)
app.include_router(rbac_router)
app.include_router(urd_router)
app.include_router(context_router)
app.include_router(clearance_router)
app.include_router(totp_router)  # /api/v1/auth/me/totp — público vía gateway
app.include_router(totp_internal_router)  # /internal/totp — NUNCA vía gateway
app.include_router(signing_key_router)  # /api/v1/auth/me/signing-key — público vía gateway
app.include_router(signing_key_admin_router)  # /api/v1/auth/admin/users/.../signing-key
app.include_router(signing_key_internal_router)  # /internal/signing-key — NUNCA vía gateway
app.include_router(audit_router)
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
