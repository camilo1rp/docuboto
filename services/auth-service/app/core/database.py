# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from collections.abc import AsyncGenerator

import asyncpg
from fastapi import HTTPException, Request, status
from orpycamcp_common.tenant import slug_valido, veredicto_entidad
from orpycamcp_common.tenant import tenant_schema as _schema_de

from app.core.config import settings

_pool: asyncpg.Pool | None = None

# El slug se valida con `orpycamcp_common.tenant` (única definición, AREA-02-05).


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
    if not slug_valido(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tenant",
                "detail": "La cabecera X-Tenant-Slug falta o no es válida",
                "status": 400,
            },
        )
    return _schema_de(slug)


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
        # Una entidad registrada que aún no tiene schema (`pending` antes de
        # `init_tenant`, o un slug que no existe) NO es un error del servidor: es
        # «sin usuario aprovisionado», el mismo caso que ya documenta `/auth/me`.
        # `SET search_path` sobre un schema inexistente no falla, así que sin esta
        # comprobación la primera consulta reventaba con `UndefinedTableError`
        # (500). `to_regclass` verifica EXACTAMENTE la precondición de `/me`
        # (que exista `auth_users` en el schema) sin tapar con un `except` la
        # deriva de esquema de un tenant que sí existe. Los vacíos no permiten
        # distinguir pending, inexistente y usuario sin aprovisionar: no es un
        # oráculo, y es fail-closed (nada se interpreta como «sin restricción»).
        aprovisionado = await conn.fetchval(
            "SELECT to_regclass(format('%I.auth_users', $1::text)) IS NOT NULL", schema
        )
        if not aprovisionado:
            yield None
            return
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


async def get_tenant_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con ``search_path`` fijado al schema del tenant (ADR-002/ADR-012).

    El tenant se toma de la cabecera ``X-Tenant-Slug`` que inyecta el api-gateway.
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    schema = tenant_schema(slug)
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        if (veto := await veredicto_entidad(conn, slug)) is not None:
            raise HTTPException(
                status_code=veto[0],
                detail={"error": veto[1], "detail": veto[2], "status": veto[0]},
            )
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            # Evita que la conexión vuelva al pool con un search_path "pegado".
            await conn.execute("SET search_path = public")
