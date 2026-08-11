# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Acceso a PostgreSQL para el historial de notificaciones (E16).

Las notificaciones se persisten en el schema del tenant (tenant_{slug}, ADR-002).
- Lecturas (endpoints HTTP): dependencia `get_tenant_conn` con search_path desde
  la cabecera X-Tenant-Slug.
- Escrituras (worker de eventos / envío): `acquire_tenant(slug)`, que fija el
  search_path para el tenant de la notificación que se está procesando.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import HTTPException, Request, status

from app.core.config import settings

_pool: asyncpg.Pool | None = None

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}$")


async def init_pool(
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
        raise RuntimeError("DB pool not initialised")
    return _pool


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


async def get_tenant_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con search_path al tenant (X-Tenant-Slug). Para endpoints de lectura."""
    schema = tenant_schema(request.headers.get("X-Tenant-Slug", ""))
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def acquire_tenant(slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con search_path al tenant `slug` (worker/escritura). Valida el slug."""
    if not slug or not _SLUG_RE.match(slug):
        raise ValueError(f"tenant slug inválido: {slug!r}")
    schema = f"tenant_{slug.replace('-', '_')}"
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
