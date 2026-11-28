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
decides the 503 on the database only: events, queues and locks live there too
(ADR-049). Everything the per-domain probes used to add is still reported but
does not take the process out of rotation: the object store, SMTP, next month's
`audit_log` partition, and each supervised consumer with its pending dead-letters.
"""

from __future__ import annotations

import aiosmtplib
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from orpycamcp_common import deadletter, events, metricas
from orpycamcp_common import health as health_common
from orpycamcp_common.database import get_pool

from auth.core.audit_partitions import estado_particion_siguiente
from docuboto.config import notification as notification_settings
from knowledge.workers.intelligence_consumer import GROUP as GRUPO_INTELIGENCIA
from notification.core.constants import DLQ_GROUP as GRUPO_NOTIFICATION
from signature.core.constants import DLQ_GROUP as GRUPO_SIGNATURE
from storage.core.minio_client import get_minio
from workflow.core.constants import DLQ_GROUP as GRUPO_WORKFLOW

router = APIRouter(tags=["health"])

_SERVICE = "docuboto"

#: Supervised consumers (`spawn_supervised` names) and their consumer groups.
CONSUMERS = {
    "workflow": GRUPO_WORKFLOW,
    "notification": GRUPO_NOTIFICATION,
    "signature": GRUPO_SIGNATURE,
    "intelligence-gateway": GRUPO_INTELIGENCIA,
}


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health() -> dict[str, str]:
    return health_common.liveness(_SERVICE)


async def _ping_db() -> None:
    async with get_pool().acquire() as conn:
        await conn.fetchval("SELECT 1")


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


async def _dlq_pendientes(grupo: str) -> int | None:
    """Dead-letters `pending` del grupo, sumados sobre las entidades. None si no se pudo."""
    try:
        async with get_pool().acquire() as conn:
            n, _ = await deadletter.contar_pendientes(
                conn, await metricas.esquemas_a_medir(conn), [grupo]
            )
        return n
    except Exception:  # noqa: BLE001 — una sonda informativa no tumba /health/ready
        return None


async def _consumidores() -> dict[str, object]:
    """Estado y DLQ de cada consumidor que arrancó. Informa, no decide el 503."""
    informes: dict[str, object] = {}
    estados = events.consumer_status()
    for nombre, grupo in CONSUMERS.items():
        estado = estados.get(nombre)
        if estado is None:
            continue
        dlq = health_common.consumer_report(estado, await _dlq_pendientes(grupo))
        informes[nombre] = {
            "consumer": "ok" if estado["corriendo"] else "reiniciando",
            "dead_letter": dlq["dead_letter"],
            "detalle": dlq["detalle"],
        }
    return informes


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready() -> JSONResponse:
    checks = {
        "database": await health_common.check("database", _ping_db),
    }
    listo = all(v == "ok" for v in checks.values())
    informativos = {
        "storage": await health_common.check("storage", _ping_storage),
        "smtp": await health_common.check("smtp", _ping_smtp),
    }
    content: dict[str, object] = {
        "status": "ready" if listo else "not_ready",
        "checks": {**checks, **informativos},
        "consumers": await _consumidores(),
    }
    if checks["database"] == "ok":
        particiones = await _audit_particiones()
        content["audit_particiones"] = particiones
        if particiones == "degraded":
            content["degraded"] = ["audit_particiones"]
    return JSONResponse(status_code=200 if listo else 503, content=content)
