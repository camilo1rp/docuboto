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
once; the domain packages keep theirs for the split layout and for tests.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from orpycamcp_common import deadletter, identidad, request_log

from archive.main import DOMAIN_ROUTERS as ARCHIVE_ROUTERS
from auth.main import DOMAIN_ROUTERS as AUTH_ROUTERS
from docuboto.core.config import settings
from docuboto.core.lifespan import lifespan
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

app = FastAPI(
    redirect_slashes=False,
    title="Docuboto",
    description="SGDEA: un proceso, los módulos de dominio por include_router.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(identidad.IdentidadFirmadaMiddleware)
app.add_middleware(request_log.RequestLogMiddleware, servicio="docuboto")
app.add_exception_handler(Problema, respuesta_problema)


@app.exception_handler(RequestValidationError)
async def _validacion_sin_eco(_request: Request, exc: RequestValidationError) -> JSONResponse:
    detalle = [
        {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detalle})


for _routers in (
    AUTH_ROUTERS,
    TENANT_ROUTERS,
    DOCUMENT_ROUTERS,
    ARCHIVE_ROUTERS,
    STORAGE_ROUTERS,
    WORKFLOW_ROUTERS,
    NOTIFICATION_ROUTERS,
    SIGNATURE_ROUTERS,
    KNOWLEDGE_ROUTERS,
):
    for _router in _routers:
        app.include_router(_router)

deadletter.instalar_manejador_radicados_ausente(app)
app.include_router(health_router)
app.include_router(metrics_router)


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
