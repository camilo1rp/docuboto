# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Barrido de fondo de document-service (AREA-09-14 / AREA-09-15).

Una pasada por TODOS los tenants activos que, por cada uno:

1. reintenta la purga de los binarios huérfanos de importaciones fallidas que storage
   no pudo borrar en el momento (`import_binario_huerfano`);
2. reanuda los lotes de radicados `pending`/`processing` que nadie está procesando
   (el servicio se reinició a media carga): el advisory lock por lote evita el doble
   procesamiento entre réplicas, y cada ítem es idempotente (sin doble numeración);
3. retira los lotes completados sin errores más antiguos que la retención.

Un fallo en un tenant (o en una fase) se registra y no frena a los demás; la
siguiente pasada lo reintenta. Corre al arrancar (tras un breve retraso) y cada
`barrido_intervalo_segundos`.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.core.config import settings
from app.core.database import acquire_tenant, get_pool
from app.services.batch_service import BatchService
from app.services.import_huerfanos_service import ImportHuerfanosService

logger = logging.getLogger(__name__)

# Una réplica a la vez por tenant en la fase de purga (su cola no es por-fila lockeada).
_ESPACIO_LOCK_PURGA = 0x50555247  # "PURG"


async def tenants_activos(pool: asyncpg.Pool) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug FROM public.tenants "
            "WHERE status = 'active' AND provisioning_status = 'active'"
        )
    return [r["slug"] for r in rows]


async def barrer_tenant(slug: str) -> dict[str, int]:
    """Las tres fases sobre un tenant. Cada una aislada de las otras."""
    resumen = {"huerfanos": 0, "lotes_reanudados": 0, "lotes_retirados": 0}
    async with acquire_tenant(slug) as conn:
        # 1. purga de huérfanos, exclusiva por tenant entre réplicas
        if await conn.fetchval("SELECT pg_try_advisory_lock($1::int, hashtext(current_schema()))", _ESPACIO_LOCK_PURGA):
            try:
                resultado = await ImportHuerfanosService(conn, slug).purgar()
                resumen["huerfanos"] = sum(resultado.values())
            except asyncpg.UndefinedTableError:
                raise
            except Exception:
                logger.exception("barrido: purga de huérfanos falló en tenant=%s", slug)
            finally:
                await conn.fetchval(
                    "SELECT pg_advisory_unlock($1::int, hashtext(current_schema()))", _ESPACIO_LOCK_PURGA
                )
        batch = BatchService(conn, slug)
        # 2. lotes interrumpidos
        try:
            resumen["lotes_reanudados"] = await batch.reanudar_interrumpidos(
                settings.batch_reanudar_despues_segundos
            )
        except asyncpg.UndefinedTableError:
            raise
        except Exception:
            logger.exception("barrido: reanudación de lotes falló en tenant=%s", slug)
        # 3. retención
        try:
            resumen["lotes_retirados"] = await batch.purgar_lotes_antiguos(settings.batch_retencion_dias)
        except asyncpg.UndefinedTableError:
            raise
        except Exception:
            logger.exception("barrido: retención de lotes falló en tenant=%s", slug)
    return resumen


async def barrer_una_vez() -> dict[str, dict[str, int]]:
    """Una pasada por todos los tenants activos. Devuelve el resumen por slug."""
    resumen: dict[str, dict[str, int]] = {}
    for slug in await tenants_activos(get_pool()):
        try:
            resumen[slug] = await barrer_tenant(slug)
        except asyncpg.UndefinedTableError:
            # Tenant sin las migraciones de este servicio: nada que barrer.
            logger.debug("barrido: tenant=%s sin tablas de document-service", slug)
        except Exception:
            logger.exception("barrido: tenant=%s omitido por un fallo", slug)
    return resumen


async def bucle_de_barrido() -> None:
    """Tarea de fondo del `lifespan`: espera un poco y barre cada intervalo."""
    await asyncio.sleep(settings.barrido_retraso_inicial_segundos)
    while True:
        try:
            await barrer_una_vez()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("barrido: pasada fallida")
        await asyncio.sleep(settings.barrido_intervalo_segundos)
