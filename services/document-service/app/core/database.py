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
from typing import TYPE_CHECKING, Any

import asyncpg
from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from app.clients.tenant_client import TenantServiceClient

_pool: asyncpg.Pool | None = None

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
    """Conexión sin contexto de tenant (health / registro global)."""
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn


def tenant_schema(slug: str) -> str:
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


def get_tenant_client(request: Request) -> "TenantServiceClient":
    """Cliente HTTP hacia tenant-service (httpx compartido en app.state)."""
    from app.clients.tenant_client import TenantServiceClient

    return TenantServiceClient(request.app.state.http_client)


def get_redis(request: Request) -> Any:
    """Cliente Redis compartido (publicación de eventos de dominio)."""
    return request.app.state.redis


async def get_tenant_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con search_path al schema del tenant (X-Tenant-Slug). ADR-002/012.

    Las tablas de radicación viven en tenant_{slug}; `public` queda en el
    search_path para resolver el registro de tenants y las extensiones globales.
    """
    schema = tenant_schema(request.headers.get("X-Tenant-Slug", ""))
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
