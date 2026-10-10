# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Mantenimiento periódico de las claves de firma personales (E17/F4).

Programa, como job del propio servicio, las dos operaciones que antes solo
existían como herramienta de ops (``app.ops.purge_revoked_signing_keys`` y
``app.ops.rewrap_signing_keys``, cuya lógica por-tenant se REUTILIZA tal cual):

 - Purga del material privado de credenciales ``revocado`` cuya ventana
   ``signing_key_purge_after_days`` venció (irreversible).
 - Re-wrap a la KEK activa, solo de filas con ``key_version`` MENOR que la activa.

Tenants ``suspended`` también se procesan: custodia de claves y reloj de
retención no dependen de que el tenant opere (la purga corre por el mismo reloj).

Mismo patrón que ``archive-service/app/jobs/indice_reconciliation.py``: tenants
desde ``public.tenants``, ``pg_try_advisory_lock`` por tenant (otra réplica con
el lock => se salta, no se espera), aislamiento de fallos por tenant, un asiento
``signing_key.maintenance_run`` por ejecución y tenant con conteos (solo cuando
hubo actividad o fallos: un barrido en vacío cada intervalo sería ruido en una
cadena de auditoría inmutable) y fail-safe:

 - Sin ``orpycamcp_common.audit`` NO se muta nada (no destruir/re-envolver sin traza).
 - Llavero de KEK inválido o ausente => se omite el re-wrap (la purga no necesita KEK).
"""

from __future__ import annotations

import asyncio
import logging
import zlib
from dataclasses import dataclass

import asyncpg
from orpycamcp_common import request_log

from app.core.config import settings
from app.core.database import tenant_schema
from app.ops import purge_revoked_signing_keys as _purge_op
from app.ops import rewrap_signing_keys as _rewrap_op

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

logger = logging.getLogger(__name__)

_SERVICE = "auth-service"
ACTION_RUN = "signing_key.maintenance_run"
ACTOR = "system:key-maintenance"


@dataclass(slots=True)
class TenantResult:
    purgadas: int = 0
    purga_fallidas: int = 0
    rewrapped: int = 0
    rewrap_fallidas: int = 0
    omitido: str | None = None  # None = ejecutado


def _lock_key(slug: str) -> int:
    """Clave de advisory lock estable por tenant (bigint con signo)."""
    return zlib.crc32(f"auth-service:signing-key-maintenance:{slug}".encode())


async def list_active_tenants(pool: asyncpg.Pool) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug FROM public.tenants "
            "WHERE status IN ('active', 'suspended') AND provisioning_status = 'active'"
        )
    return [r["slug"] for r in rows]


def _keyring_or_none() -> tuple[int, bytes, dict[int, bytes]] | None:
    try:
        active_v, active_key = settings.signing_key_active_key()
        return active_v, active_key, settings.signing_key_keyring()
    except Exception as exc:  # noqa: BLE001 — llavero ausente/ inválido => fail-safe
        logger.warning("signing-key maintenance: re-wrap omitido, llavero KEK no disponible: %s", exc)
        return None


async def run_tenant(conn: asyncpg.Connection, slug: str) -> TenantResult:
    """Una pasada de mantenimiento para UN tenant, bajo advisory lock."""
    if not _AUDIT_AVAILABLE:
        logger.error("signing-key maintenance: auditoría no disponible; no se muta nada")
        return TenantResult(omitido="sin_auditoria")

    key = _lock_key(slug)
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", key):
        return TenantResult(omitido="lock_ocupado")
    try:
        schema = tenant_schema(slug)
        res = TenantResult()
        res.purgadas, res.purga_fallidas = await _purge_op._purge_tenant(
            conn,
            schema=schema,
            slug=slug,
            days=settings.signing_key_purge_after_days,
            dry_run=False,
        )
        ring = _keyring_or_none()
        if ring is not None:
            active_v, active_key, keyring = ring
            res.rewrapped, res.rewrap_fallidas = await _rewrap_op._rewrap_tenant(
                conn,
                schema=schema,
                slug=slug,
                active_v=active_v,
                active_key=active_key,
                keyring=keyring,
                dry_run=False,
            )
        if res.purgadas or res.purga_fallidas or res.rewrapped or res.rewrap_fallidas:
            await conn.execute("SET search_path = public")
            await _audit.append(
                conn,
                tenant_slug=slug,
                service=_SERVICE,
                action=ACTION_RUN,
                object_type="signing_key",
                object_ref=slug,
                actor=ACTOR,
                canal="system",
                payload={
                    "purgadas": res.purgadas,
                    "purga_fallidas": res.purga_fallidas,
                    "rewrapped": res.rewrapped,
                    "rewrap_fallidas": res.rewrap_fallidas,
                    "rewrap_omitido": ring is None,
                    "purge_after_days": settings.signing_key_purge_after_days,
                },
            )
        return res
    finally:
        try:
            await conn.execute("SET search_path = public")
            await conn.execute("SELECT pg_advisory_unlock($1)", key)
        except Exception:  # noqa: BLE001
            pass


async def run_once(pool: asyncpg.Pool) -> dict[str, TenantResult]:
    """Barre todos los tenants activos; un fallo en uno no aborta el resto."""
    out: dict[str, TenantResult] = {}
    for slug in await list_active_tenants(pool):
        try:
            async with pool.acquire() as conn:
                out[slug] = await run_tenant(conn, slug)
        except Exception:  # noqa: BLE001
            logger.exception("signing-key maintenance: fallo en el tenant %s", slug)
    return out


async def maintenance_loop(pool: asyncpg.Pool) -> None:
    """Loop del lifespan: nunca muere por una excepción."""
    request_log.canal_de_fondo()  # proceso de fondo: sus asientos son del canal 'system' (AREA-03-07)
    while True:
        try:
            await run_once(pool)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("signing-key maintenance: fallo en la barrida")
        await asyncio.sleep(settings.signing_key_maintenance_interval_seconds)
