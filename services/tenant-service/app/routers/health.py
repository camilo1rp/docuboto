# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de tenant-service.

`/health` comprobaba la base de datos y era, a la vez, el `healthcheck` del
contenedor: tomaba una conexión del pool **en cada sondeo** (cada 15 segundos)
para preguntar `SELECT 1`, y metía una dependencia externa en la sonda que
gobierna los reinicios. Se reparte en dos, como en el resto del sistema — ver
`orpycamcp_common.health` para el criterio completo.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from orpycamcp_common import health as health_common

from app.core.database import get_pool

router = APIRouter(tags=["health"])

_SERVICE = "tenant-service"


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health() -> dict[str, str]:
    """Sin dependencias, a propósito: gobierna los reinicios del contenedor."""
    return health_common.liveness(_SERVICE)


async def _ping_db() -> None:
    async with get_pool().acquire() as conn:
        await conn.fetchval("SELECT 1")


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready() -> JSONResponse:
    checks = {
        "database": await health_common.check("database", _ping_db),
    }

    listo = checks["database"] == "ok"
    return JSONResponse(
        status_code=200 if listo else 503,
        content={"status": "ready" if listo else "not_ready", "checks": checks},
    )
