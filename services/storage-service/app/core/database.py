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

# Validación del slug antes de interpolarlo en `SET search_path` (gemelo de
# archive-service/document-service `_SLUG_RE`, ADR-002/012). Introducido para el
# gate RBAC de preservación WORM (`core/clearance.py`), donde el nombre del bucket
# WORM se deriva de este mismo slug — nunca del body de la petición.
#
# Público (no `_SLUG_RE`) porque es la fuente única canónica del servicio:
# `app.repositories.file_repository.FileRepository` y
# `app.repositories.preservacion_repository.PreservacionRepository` la
# reusan en vez de duplicar la regex (remediación de auditoría Media, E07 —
# antes `FileRepository` tenía su propia regex sin guiones, que rechazaba
# la forma canónica y habitual de un `tenant_slug`).
TENANT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,54}$")


def tenant_schema(slug: str) -> str:
    """Devuelve el identificador de schema `tenant_{slug}` validado (fail-closed 400).

    Normaliza guiones a guiones bajos. Úsese SIEMPRE entrecomillado:
    ``f'SET search_path TO "{tenant_schema(slug)}", public'``.
    """
    if not slug or not TENANT_SLUG_RE.match(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tenant",
                "detail": "X-Tenant-Slug header missing or invalid",
                "status": 400,
            },
        )
    return f"tenant_{slug.replace('-', '_')}"


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
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        try:
            yield conn
        finally:
            # Defensa en profundidad: la conexión vuelve al pool con search_path
            # neutro, no heredando el schema del último tenant atendido.
            await conn.execute("SET search_path = public")
