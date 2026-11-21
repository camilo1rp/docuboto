# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One `/health` and one `/health/ready` for the process (ADR-047).

`/health` has no dependencies: it governs container restarts. `/health/ready`
decides the 503 on the database and Redis only. Everything the per-domain
probes used to add is still reported but does not take the process out of
rotation: Keycloak, the object store, SMTP, next month's `audit_log`
partition, and each supervised consumer with its dead-letter stream.
"""

from __future__ import annotations

import aiosmtplib
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from orpycamcp_common import events
from orpycamcp_common import health as health_common

from auth.core.audit_partitions import estado_particion_siguiente
from auth.routers.auth import get_auth_service
from document.core.database import get_pool
from notification.core.config import settings as notification_settings
from notification.workers.stream_worker import DEADLETTER_STREAM as DLQ_NOTIFICATION
from signature.workers.document_consumer import DEADLETTER_STREAM as DLQ_SIGNATURE
from storage.core.minio_client import get_minio
from workflow.workers.document_consumer import DEADLETTER_STREAM as DLQ_WORKFLOW

router = APIRouter(tags=["health"])

_SERVICE = "docuboto"

#: Supervised consumers and their dead-letter streams (`spawn_supervised` names).
CONSUMERS = {
    "workflow": DLQ_WORKFLOW,
    "notification": DLQ_NOTIFICATION,
    "signature": DLQ_SIGNATURE,
}


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health() -> dict[str, str]:
    return health_common.liveness(_SERVICE)


async def _ping_db() -> None:
    async with get_pool().acquire() as conn:
        await conn.fetchval("SELECT 1")


def _redis(request: Request):  # noqa: ANN202 — redis.asyncio.Redis | None
    return getattr(request.app.state, "redis", None)


async def _ping_redis(request: Request) -> None:
    redis = _redis(request)
    if redis is None:
        raise RuntimeError("redis")
    await redis.ping()


async def _ping_keycloak() -> None:
    if not await get_auth_service().keycloak_healthy():
        raise RuntimeError("keycloak no responde")


async def _ping_storage() -> None:
    await get_minio().list_buckets()


async def _ping_smtp() -> None:
    smtp = aiosmtplib.SMTP(
        hostname=notification_settings.smtp_host,
        port=notification_settings.smtp_port,
        timeout=3,
    )
    await smtp.connect()
    await smtp.quit()


async def _audit_particiones() -> str:
    try:
        async with get_pool().acquire() as conn:
            return await estado_particion_siguiente(conn)
    except Exception:  # noqa: BLE001 — una sonda informativa no tumba /health/ready
        return "unknown"


async def _consumidores(request: Request) -> dict[str, object]:
    """Estado y DLQ de cada consumidor que arrancó. Informa, no decide el 503."""
    informes: dict[str, object] = {}
    estados = events.consumer_status()
    redis = _redis(request)
    for nombre, stream in CONSUMERS.items():
        estado = estados.get(nombre)
        if estado is None:
            continue
        informe: dict[str, object] = {"consumer": "ok" if estado["corriendo"] else "reiniciando"}
        if redis is not None:
            dlq = await health_common.consumer_report(redis, estado, stream)
            informe["dead_letter"] = dlq["dead_letter"]
            informe["detalle"] = dlq["detalle"]
        informes[nombre] = informe
    return informes


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready(request: Request) -> JSONResponse:
    checks = {
        "database": await health_common.check("database", _ping_db),
        "redis": await health_common.check("redis", lambda: _ping_redis(request)),
    }
    listo = all(v == "ok" for v in checks.values())
    informativos = {
        "keycloak": await health_common.check("keycloak", _ping_keycloak),
        "storage": await health_common.check("storage", _ping_storage),
        "smtp": await health_common.check("smtp", _ping_smtp),
    }
    content: dict[str, object] = {
        "status": "ready" if listo else "not_ready",
        "checks": {**checks, **informativos},
        "consumers": await _consumidores(request),
    }
    if checks["database"] == "ok":
        particiones = await _audit_particiones()
        content["audit_particiones"] = particiones
        if particiones == "degraded":
            content["degraded"] = ["audit_particiones"]
    return JSONResponse(status_code=200 if listo else 503, content=content)
