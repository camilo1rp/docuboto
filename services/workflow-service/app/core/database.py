# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import Request

_pool: asyncpg.Pool | None = None


async def create_pool(dsn: str, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


async def get_db_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def acquire_tenant(tenant_slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con search_path al tenant `slug` (worker de distribución).

    El tenant_slug viene del sobre del evento (emitido por document-service);
    se confía como las demás rutas (lo valida el api-gateway en el flujo HTTP).
    """
    schema = f"tenant_{tenant_slug.replace('-', '_')}"
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
