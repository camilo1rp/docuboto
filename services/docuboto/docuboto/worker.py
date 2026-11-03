# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Worker process: health plus the heavy claim loop. No domain HTTP surface."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from docuboto.core.config import settings
from docuboto.core.lifespan_worker import lifespan
from docuboto.routers.health import router as health_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

app = FastAPI(
    redirect_slashes=False,
    title="Docuboto worker",
    description="veraPDF, render, and LTA. No decryption secrets.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
app.include_router(health_router)
