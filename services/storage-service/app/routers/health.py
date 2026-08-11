# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de storage-service.

`/health` comprobaba la base de datos **y listaba los buckets de MinIO**, y era a
la vez el `healthcheck` del contenedor: tomaba una conexión del pool y hacía una
llamada al almacén de objetos **cada 15 segundos**, además de meter dos
dependencias externas en la sonda que gobierna los reinicios. Se reparte en dos,
como en el resto del sistema — ver `orpycamcp_common.health`.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from orpycamcp_common import health as health_common

from app.core.database import get_pool
from app.core.minio_client import get_minio

router = APIRouter(tags=["health"])

_SERVICE = "storage-service"


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health() -> dict[str, str]:
    """Sin dependencias, a propósito: gobierna los reinicios del contenedor."""
    return health_common.liveness(_SERVICE)


async def _ping_db() -> None:
    async with get_pool().acquire() as conn:
        await conn.fetchval("SELECT 1")


async def _ping_minio() -> None:
    # `list_buckets` es la comprobación de conectividad más barata del cliente.
    await get_minio().list_buckets()


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready() -> JSONResponse:
    checks = {
        "database": await health_common.check("database", _ping_db),
        # MinIO SÍ es bloqueante aquí: sin el almacén de objetos este servicio no
        # puede ni subir ni servir un anexo, que es todo lo que hace.
        "storage": await health_common.check("storage", _ping_minio),
    }

    listo = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if listo else 503,
        content={"status": "ready" if listo else "not_ready", "checks": checks},
    )
