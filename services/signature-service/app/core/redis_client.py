# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Redis client lifecycle helpers for signature-service (worker only).

The HTTP routers do not use Redis directly; this module is consumed by the
document_consumer worker that syncs nivel_seguridad snapshots on reclassification
events (``document.radicado.reclassified`` stream).

Pattern copied from workflow-service/app/core/redis_client.py.
"""

import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None


async def init_redis(url: str) -> aioredis.Redis:
    global _redis
    client: aioredis.Redis = await aioredis.from_url(url, decode_responses=True)
    _redis = client
    return client


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def get_redis() -> aioredis.Redis:
    redis = _redis
    if redis is None:
        raise RuntimeError("Redis not initialised — call init_redis() in lifespan")
    return redis
