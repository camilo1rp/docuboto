# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de knowledge-service.

`/health` devolvía un `{"status": "ok"}` **fijo, sin tocar nada**, y era el
`healthcheck` del contenedor. Aquí eso es especialmente engañoso: si el
consumidor de ingesta muere, el índice pgvector se queda congelado en el pasado
y el servicio sigue respondiendo búsquedas — con resultados incompletos y sin
que nada lo delate. Ver `orpycamcp_common.health` para el reparto entre sondas.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from orpycamcp_common import events
from orpycamcp_common import health as health_common

from app.core.database import get_pool
from app.core.redis_client import get_redis

router = APIRouter(tags=["health"])

_SERVICE = "knowledge-service"
_CONSUMIDOR = "knowledge"


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health() -> dict[str, str]:
    return health_common.liveness(_SERVICE)


async def _ping_db() -> None:
    async with get_pool().acquire() as conn:
        await conn.fetchval("SELECT 1")


async def _ping_redis() -> None:
    await get_redis().ping()


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready() -> JSONResponse:
    checks = {
        "database": await health_common.check("database", _ping_db),
        "redis": await health_common.check("redis", _ping_redis),
    }

    estado = events.consumer_status().get(_CONSUMIDOR)
    if estado is not None:
        checks["consumer"] = "ok" if estado["corriendo"] else "reiniciando"

    listo = checks["database"] == "ok" and checks["redis"] == "ok"
    return JSONResponse(
        status_code=200 if listo else 503,
        content={"status": "ready" if listo else "not_ready", "checks": checks},
    )
