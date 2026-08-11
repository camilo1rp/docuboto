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

from app.core.config import settings

_pool: asyncpg.Pool | None = None

# slug validado por tenant-service (minúsculas, dígitos, guiones).
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}$")


async def create_pool(
    dsn: str, min_size: int | None = None, max_size: int | None = None
) -> asyncpg.Pool:
    """Crea el pool del servicio.

    Los tamaños y el tope por consulta vienen de la configuración (ver
    `db_pool_max_size` en `app/core/config.py` y la aritmética del
    presupuesto en `infra/docker-compose.prod.yml`). Los argumentos siguen
    aceptándose para que los tests fijen un tamaño propio.
    """
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size if min_size is not None else settings.db_pool_min_size,
        max_size=max_size if max_size is not None else settings.db_pool_max_size,
        command_timeout=settings.db_command_timeout,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Pool del proceso, para quien no tiene `Request` (sondas de salud, jobs)."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


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


async def get_optional_tenant_conn(
    request: Request,
) -> AsyncGenerator[asyncpg.Connection | None, None]:
    """Tenant-scoped connection when ``X-Tenant-Slug`` is present; ``None`` otherwise.

    Used by endpoints that must serve both tenant-scoped users and tenant-less
    superadmins (e.g. ``GET /auth/me``). Callers must treat ``None`` as
    "no tenant context" and apply fail-closed defaults (empty permission lists,
    not full access).
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    if not slug:
        yield None
        return
    schema = tenant_schema(slug)  # raises HTTP 400 if slug is syntactically invalid
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


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
