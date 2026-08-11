# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de signature-service.

`/health` devolvía un `{"status": "ok"}` **fijo, sin tocar nada**, y ese endpoint
era el `healthcheck` del contenedor: el servicio que firma el índice electrónico
y sella las actas de transferencia se reportaba sano con la base de datos caída,
sin Redis, o con el consumidor de reclasificaciones muerto — el que mantiene
`firma_solicitud.nivel_seguridad` al día con el nivel real del radicado
(RF-SEG-08). Ver `orpycamcp_common.health` para el reparto entre las dos sondas.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from orpycamcp_common import events
from orpycamcp_common import health as health_common

from app.core.database import get_pool
from app.core.redis_client import get_redis

router = APIRouter(tags=["health"])

_SERVICE = "signature-service"
_CONSUMIDOR = "signature"


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

    # El consumidor no es una dependencia para ATENDER peticiones, así que su
    # estado se informa pero no decide el 503: el servicio puede firmar aunque
    # la resincronización de niveles esté reintentando. Que aparezca aquí es lo
    # que hace visible un consumidor muerto, que era el problema.
    estado = events.consumer_status().get(_CONSUMIDOR)
    if estado is not None:
        checks["consumer"] = "ok" if estado["corriendo"] else "reiniciando"

    listo = checks["database"] == "ok" and checks["redis"] == "ok"
    return JSONResponse(
        status_code=200 if listo else 503,
        content={"status": "ready" if listo else "not_ready", "checks": checks},
    )
