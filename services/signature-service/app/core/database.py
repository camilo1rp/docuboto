# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import HTTPException, Request, status

from app.core.config import settings

_pool: asyncpg.Pool | None = None

# Slug length bound: "tenant_" (7) + slug (≤ 55) = 62 ≤ 63 bytes (PG identifier limit).
# Longer slugs with a common prefix would truncate to the same schema → cross-tenant leak.
# Mirrors workflow-service/app/core/database.py (security hardening, hallazgo 1.1).
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,54}$")


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
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call create_pool() in lifespan")
    return _pool


def _schema(slug: str) -> str:
    """Validates the tenant slug and returns the PostgreSQL schema name.

    Raises ValueError for invalid slugs (fail-closed: no SQL is ever executed
    with an unvalidated identifier).  Callers translate to the appropriate
    exception type for their context:
    - HTTP handlers (get_tenant_conn): catch ValueError → HTTPException(400).
    - Worker loops (acquire_tenant): let ValueError propagate so the loop can
      log it without acking the message (non-retryable invalid-slug event).
    """
    if not slug or not _SLUG_RE.match(slug):
        raise ValueError(f"Invalid tenant slug: {slug!r}")
    return f"tenant_{slug.replace('-', '_')}"


async def get_tenant_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    try:
        schema = _schema(request.headers.get("X-Tenant-Slug", ""))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tenant",
                "detail": "X-Tenant-Slug inválido o ausente",
                "status": 400,
            },
        ) from exc
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def acquire_tenant(tenant_slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Yields a connection with search_path set to the tenant's schema.

    Used by the document_consumer worker to handle reclassification events coming
    from Redis Streams.  The tenant_slug originates from the trusted event envelope
    emitted by document-service; the slug is still validated (fail-closed) before
    interpolation to prevent schema injection if the envelope were tampered.

    Raises ValueError for invalid slugs — the worker loop logs this and continues
    without acking (non-retryable: the slug is permanently invalid).
    """
    schema = _schema(tenant_slug)  # raises ValueError on invalid slug
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
