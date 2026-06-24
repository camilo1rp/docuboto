# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import re
from collections.abc import AsyncGenerator

import asyncpg
from fastapi import HTTPException, Request, status

_pool: asyncpg.Pool | None = None

# slug validado por tenant-service (minúsculas, dígitos, guiones).
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}$")


async def create_pool(dsn: str, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_db_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión sin contexto de tenant (operaciones globales / health)."""
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn


def tenant_schema(slug: str) -> str:
    """Nombre de schema seguro a partir de un slug validado."""
    if not slug or not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tenant",
                "detail": "X-Tenant-Slug header missing or invalid",
                "status": 400,
            },
        )
    return f"tenant_{slug.replace('-', '_')}"


async def get_tenant_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con ``search_path`` fijado al schema del tenant (ADR-002/ADR-012).

    El tenant se toma de la cabecera ``X-Tenant-Slug`` que inyecta el api-gateway.
    """
    schema = tenant_schema(request.headers.get("X-Tenant-Slug", ""))
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            # Evita que la conexión vuelva al pool con un search_path "pegado".
            await conn.execute("SET search_path = public")
