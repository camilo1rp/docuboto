# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de notification-service.

`/health` era el `healthcheck` del contenedor y hacía tres cosas que no debía:
devolvía **503** cuando Redis no respondía (el orquestador reinicia el servicio,
que no arregla Redis y sí pierde el trabajo en vuelo), **abría una conexión SMTP
completa en cada sondeo** —cada 15 segundos, contra el servidor de correo de la
institución, que puede interpretarlo como abuso— y publicaba el motivo del fallo
como ``f"error: {exc}"``, con el nombre y el puerto del servidor de correo
dentro. Ver `orpycamcp_common.health` para el reparto entre las dos sondas.
"""

from __future__ import annotations

import aiosmtplib
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from orpycamcp_common import events
from orpycamcp_common import health as health_common

from app.core.config import settings
from app.core.database import get_pool
from app.core.redis_client import get_redis
from app.workers.stream_worker import DEADLETTER_STREAM

router = APIRouter(tags=["health"])

_SERVICE = "notification-service"
_CONSUMIDOR = "notification"


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health_check() -> dict[str, str]:
    return health_common.liveness(_SERVICE)


async def _ping_db() -> None:
    async with get_pool().acquire() as conn:
        await conn.fetchval("SELECT 1")


async def _ping_redis() -> None:
    await get_redis().ping()


async def _ping_smtp() -> None:
    smtp = aiosmtplib.SMTP(hostname=settings.smtp_host, port=settings.smtp_port)
    await smtp.connect()
    await smtp.quit()


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready() -> JSONResponse:
    checks = {
        "database": await health_common.check("database", _ping_db),
        "redis": await health_common.check("redis", _ping_redis),
        # SMTP se informa pero NO decide el 503: un servidor de correo caído no
        # impide encolar notificaciones, y sacar el servicio del balanceador por
        # eso apagaría también el histórico y los webhooks. La conexión ya no se
        # abre en cada sondeo del contenedor, solo cuando alguien pregunta aquí.
        "smtp": await health_common.check("smtp", _ping_smtp),
    }

    detalle = None
    estado = events.consumer_status().get(_CONSUMIDOR)
    if estado is not None:
        checks["consumer"] = "ok" if estado["corriendo"] else "reiniciando"
        # 13-12: tamaño de la DLQ y último barrido de reclamación. Informa, no decide el 503.
        informe = await health_common.consumer_report(get_redis(), estado, DEADLETTER_STREAM)
        checks["dead_letter"] = informe["dead_letter"]
        detalle = informe["detalle"]

    listo = checks["database"] == "ok" and checks["redis"] == "ok"
    return JSONResponse(
        status_code=200 if listo else 503,
        content={
            "status": "ready" if listo else "not_ready",
            "checks": checks,
            "consumer": detalle,
        },
    )
