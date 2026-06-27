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
from contextlib import asynccontextmanager

import asyncpg
from fastapi import HTTPException, Request, status

_pool: asyncpg.Pool | None = None

# Validación del slug antes de interpolarlo en `SET search_path` (defensa en
# profundidad, gemelo de document-service `_SLUG_RE`). El slug llega del JWT
# re-derivado por el gateway, pero validarlo cierra el sink de inyección de schema
# si el claim se contaminara en aprovisionamiento/Keycloak.
# Largo acotado a 55 (+ "tenant_" = 62) para no exceder el límite de 63 bytes de
# identificador de PostgreSQL: dos slugs largos de prefijo común se truncarían al
# mismo schema → fuga cross-tenant. (auditoría hardening, hallazgo 1.1)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,54}$")


def tenant_schema(slug: str) -> str:
    """Devuelve el identificador de schema `tenant_{slug}` validado (fail-closed 400).

    Normaliza guiones a guiones bajos. El resultado se usa SIEMPRE entrecomillado:
    ``f'SET search_path TO "{tenant_schema(slug)}", public'``.
    """
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
        try:
            yield conn
        finally:
            # Defensa en profundidad: la conexión vuelve al pool con search_path
            # neutro (no hereda el schema del último tenant). Cada consumidor lo
            # fija explícitamente vía tenant_schema antes de consultar.
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def acquire_tenant(tenant_slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con search_path al tenant `slug` (worker de distribución).

    El tenant_slug viene del sobre del evento (emitido por document-service);
    se confía como las demás rutas (lo valida el api-gateway en el flujo HTTP).
    """
    schema = tenant_schema(tenant_slug)
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
