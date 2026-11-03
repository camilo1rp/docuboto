# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One `/health` and one `/health/ready` for the process."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import health as health_common

from document.core.database import get_pool

router = APIRouter(tags=["health"])

_SERVICE = "docuboto"


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health() -> dict[str, str]:
    return health_common.liveness(_SERVICE)


async def _ping_db() -> None:
    async with get_pool().acquire() as conn:
        await conn.fetchval("SELECT 1")


async def _ping_redis(request: Request) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RuntimeError("redis")
    await redis.ping()


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready(request: Request) -> JSONResponse:
    checks = {
        "database": await health_common.check("database", _ping_db),
        "redis": await health_common.check("redis", lambda: _ping_redis(request)),
    }
    listo = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if listo else 503,
        content={"status": "ready" if listo else "not_ready", "checks": checks},
    )
