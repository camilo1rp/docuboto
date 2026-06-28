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
from app.routers.cadena import router as cadena_router
from app.routers.health import router as health_router
from app.routers.signature import router as signature_router

logging.basicConfig(level=settings.log_level.upper())

logger = logging.getLogger("signature.startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Fail-fast visible del contrato de issuer: deja en el log el `iss` esperado
    # y avisa si la configuración rechazaría todos los tokens (ver config.py).
    logger.info("JWT issuer esperado (iss): %s", settings.expected_issuer)
    for warning in settings.issuer_config_warnings():
        logger.warning(warning)

    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    yield
    await close_pool()


app = FastAPI(
    title="OrpycaMCP — Signature Service",
    description=(
        "Firma electrónica (E06, ADR-016): sign-xml + verify. Proveedor pluggable "
        "(nativo hash+identidad por defecto; PKI XAdES/PAdES/TSA como swap)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(signature_router)
app.include_router(cadena_router)
app.include_router(health_router)
