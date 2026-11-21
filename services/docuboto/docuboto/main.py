# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""The Docuboto process: domain routers on one FastAPI app.

`include_router` mounts each module's routes. A mounted sub-app would swallow
the other module's 404 and skip this lifespan. Health and metrics live here
once. `create_app` is the one definition of middleware and handlers: the tests
build a domain-scoped app with it instead of keeping one per domain.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from orpycamcp_common import deadletter, request_log, usuario

from archive.main import DOMAIN_ROUTERS as ARCHIVE_ROUTERS
from auth.main import DOMAIN_ROUTERS as AUTH_ROUTERS
from docuboto.core.config import settings
from docuboto.core.lifespan import lifespan
from docuboto.core.security import SecurityHeadersMiddleware, UserRateLimitMiddleware
from docuboto.routers.health import router as health_router
from docuboto.routers.metrics import router as metrics_router
from document.main import DOMAIN_ROUTERS as DOCUMENT_ROUTERS
from knowledge.main import DOMAIN_ROUTERS as KNOWLEDGE_ROUTERS
from knowledge.routers.retorno import Problema, respuesta_problema
from notification.main import DOMAIN_ROUTERS as NOTIFICATION_ROUTERS
from signature.main import DOMAIN_ROUTERS as SIGNATURE_ROUTERS
from storage.main import DOMAIN_ROUTERS as STORAGE_ROUTERS
from tenant.main import DOMAIN_ROUTERS as TENANT_ROUTERS
from workflow.main import DOMAIN_ROUTERS as WORKFLOW_ROUTERS

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

#: Every domain's routers, in mounting order.
ALL_ROUTERS: tuple[APIRouter, ...] = (
    *AUTH_ROUTERS,
    *TENANT_ROUTERS,
    *DOCUMENT_ROUTERS,
    *ARCHIVE_ROUTERS,
    *STORAGE_ROUTERS,
    *WORKFLOW_ROUTERS,
    *NOTIFICATION_ROUTERS,
    *SIGNATURE_ROUTERS,
    *KNOWLEDGE_ROUTERS,
)


async def _validacion_sin_eco(_request: Request, exc: RequestValidationError) -> JSONResponse:
    detalle = [
        {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detalle})


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


def create_app(
    routers: Iterable[APIRouter] = ALL_ROUTERS,
    *,
    with_lifespan: bool = True,
    with_ops: bool = True,
    rate_limit: bool = True,
) -> FastAPI:
    """The process app. Tests pass one domain's routers and turn the rest off."""
    nueva = FastAPI(
        redirect_slashes=False,
        title="Docuboto",
        description="SGDEA: un proceso, los módulos de dominio por include_router.",
        version="2.0.0",
        lifespan=lifespan if with_lifespan else None,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    # Starlette ejecuta en orden inverso al de registro: SecurityHeaders (el
    # identificador de petición) → RequestLog → UsuarioActual → cuota por persona
    # → routers.
    if rate_limit:
        nueva.add_middleware(UserRateLimitMiddleware)
    nueva.add_middleware(usuario.UsuarioActualMiddleware)
    nueva.add_middleware(request_log.RequestLogMiddleware, servicio="docuboto")
    nueva.add_middleware(SecurityHeadersMiddleware)
    nueva.add_exception_handler(Problema, respuesta_problema)
    nueva.add_exception_handler(RequestValidationError, _validacion_sin_eco)
    for router in routers:
        nueva.include_router(router)
    deadletter.instalar_manejador_radicados_ausente(nueva)
    if with_ops:
        nueva.include_router(health_router)
        nueva.include_router(metrics_router)
    nueva.add_exception_handler(Exception, unhandled_exception_handler)
    return nueva


app = create_app()
