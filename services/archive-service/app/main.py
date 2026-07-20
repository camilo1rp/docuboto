# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
archive-service — FastAPI application factory.

Responsibilities:
- Expediente management (grouping of related radicados by matter/case)
- TRD (Tabla de Retención Documental) catalogue — retention schedules and dispositions
- Tenant-isolated storage: PostgreSQL schema per tenant (SET search_path)
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.jobs.indice_reconciliation import run_once as _reconcile_indices_once
from app.jobs.indice_reconciliation import run_once_actas as _reconcile_actas_once
from app.jobs.indice_reconciliation import run_once_actas_worm as _reconcile_actas_worm_once
from app.jobs.indice_reconciliation import run_once_worm as _reconcile_worm_once
from app.jobs.indice_reconciliation import (
    run_once_worm_renovacion as _reconcile_worm_renovacion_once,
)
from app.routers.batch import router as batch_router
from app.routers.expedientes import router as expedientes_router
from app.routers.fisico import router as fisico_router
from app.routers.health import router as health_router
from app.routers.indice import router as indice_router
from app.routers.metadata import router as metadata_router
from app.routers.tipos_documentales import router as tipos_documentales_router
from app.routers.transferencias import router as transferencias_router
from app.routers.trd import router as trd_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def _indice_reconciliation_loop() -> None:
    """Loop de fondo (residual Ac. AGN 001/2024 art. 4.3.2.4): barre todos
    los tenants activos cada `indice_reconciliation_interval_seconds`. Un
    fallo de UNA barrida (p. ej. Postgres momentáneamente inalcanzable)
    queda contenido aquí — el loop NUNCA muere por una excepción, solo
    reintenta en la siguiente iteración.

    Incluye la 3ra fase (E10 Inc.1, cableado WORM del índice,
    `run_once_worm`) en el MISMO ciclo/cadencia que el sellado — está
    tan estrechamente acoplada a "el índice ya quedó 'firmado'" que no
    amerita un kill-switch/intervalo propio (a diferencia del acta, que sí
    tiene cadencia dedicada por ser aditiva y menos urgente, ver
    `_acta_reconciliation_loop`). Un fallo en la fase WORM tampoco debe
    impedir que la siguiente iteración reintente el sellado.

    Incluye también la 5ª fase (E10 debt, renovación de retención WORM para
    series CT, `run_once_worm_renovacion`) — piggyback en el MISMO ciclo por
    el mismo criterio: reactiva a "el índice/acta ya quedó protegido", sin
    kill-switch/intervalo propio (solo la ventana de antelación
    `worm_renovacion_ventana_dias` es configurable). Cubre ÍNDICE Y ACTA en
    una sola llamada (ver el docstring de `run_once_worm_renovacion`)."""
    while True:
        try:
            await _reconcile_indices_once()
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación del sellado del índice",
                exc_info=True,
            )
        try:
            await _reconcile_worm_once()
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación WORM del índice",
                exc_info=True,
            )
        try:
            await _reconcile_worm_renovacion_once()
        except Exception:
            logger.warning(
                "Fallo en la barrida de renovación de retención WORM (series CT)",
                exc_info=True,
            )
        await asyncio.sleep(settings.indice_reconciliation_interval_seconds)


async def _acta_reconciliation_loop() -> None:
    """Loop de fondo GEMELO (H-H, E06/F4) para el sellado del acta de
    transferencia — cadencia PROPIA y MENOR
    (`settings.acta_reconciliation_interval_seconds`, default mayor que la
    del índice: el sellado del acta es aditivo, menos urgente). Igual que
    `_indice_reconciliation_loop`, el loop NUNCA muere por una excepción.

    Incluye la 4ª fase (E10 Inc.2 wiring, cableado WORM del acta,
    `run_once_actas_worm`) en el MISMO ciclo/cadencia que el sellado — mismo
    criterio que ató `run_once_worm` al ciclo del índice
    (`_indice_reconciliation_loop`): tan estrechamente acoplada a "el acta ya
    quedó 'firmado'" que no amerita kill-switch/intervalo propio. Un fallo en
    la fase WORM tampoco debe impedir que la siguiente iteración reintente el
    sellado."""
    while True:
        try:
            await _reconcile_actas_once()
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación del sellado del acta",
                exc_info=True,
            )
        try:
            await _reconcile_actas_worm_once()
        except Exception:
            logger.warning(
                "Fallo en la barrida de reconciliación WORM del acta",
                exc_info=True,
            )
        await asyncio.sleep(settings.acta_reconciliation_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("archive-service starting up")
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    reconcile_task: asyncio.Task[None] | None = None
    if settings.indice_reconciliation_enabled:
        reconcile_task = asyncio.create_task(_indice_reconciliation_loop())
    acta_reconcile_task: asyncio.Task[None] | None = None
    if settings.acta_reconciliation_enabled:
        acta_reconcile_task = asyncio.create_task(_acta_reconciliation_loop())
    yield
    logger.info("archive-service shutting down")
    if reconcile_task is not None:
        reconcile_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconcile_task
    if acta_reconcile_task is not None:
        acta_reconcile_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await acta_reconcile_task
    await close_pool()


app = FastAPI(
    redirect_slashes=False,
    title="OrpycaMCP — Archive Service",
    description=(
        "Manages expedientes (logical document groupings) and the Tabla de Retención "
        "Documental (TRD). Provides complete lifecycle tracking from creation to "
        "final disposition in accordance with Ley 594/2000."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(batch_router)
app.include_router(expedientes_router)
app.include_router(indice_router)
app.include_router(fisico_router)
app.include_router(transferencias_router)
app.include_router(metadata_router)
app.include_router(tipos_documentales_router)
app.include_router(trd_router)
app.include_router(health_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "An unexpected error occurred",
            "status": 500,
        },
    )
