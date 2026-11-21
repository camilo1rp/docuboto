# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La base de datos del proceso: un pool y las conexiones de tenant (ADR-047).

Antes había nueve copias de este módulo, una por servicio, con dos nombres para
tres cosas distintas. Ahora son tres dependencias con nombre propio:

* `get_conn`: conexión al pool, sin tenant (`public`).
* `get_vetted_conn`: conexión que ya pasó el veto de entidad (si la petición trae
  tenant), pero sin `search_path`; el consumidor lo fija con `tenant_schema`.
* `get_tenant_conn`: veto de entidad y `search_path` al schema del tenant.

Y `acquire_tenant` para lo que corre fuera de una petición (jobs, `api.py`).
Toda conexión vuelve al pool con `search_path = public`: nunca hereda el schema
del último tenant.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import HTTPException, Request, status

from orpycamcp_common.tenant import slug_valido, veredicto_entidad
from orpycamcp_common.tenant import tenant_schema as _schema_de
from orpycamcp_common.usuario import slug_de

_pool: asyncpg.Pool | None = None


class SlugInvalido(HTTPException, ValueError):
    """Slug de tenant mal formado: 400 en el borde HTTP, `ValueError` en un job."""

    def __init__(self) -> None:
        HTTPException.__init__(
            self,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tenant",
                "detail": "Identificador de tenant inválido",
                "status": 400,
            },
        )


def tenant_schema(slug: str) -> str:
    """`tenant_{slug}` validado; `SlugInvalido` si el slug no tiene forma.

    Úsese SIEMPRE entrecomillado: ``f'SET search_path = "{tenant_schema(slug)}"'``.
    """
    if not slug_valido(slug):
        raise SlugInvalido()
    return _schema_de(slug)


async def create_pool(
    dsn: str, *, min_size: int, max_size: int, command_timeout: float
) -> asyncpg.Pool:
    """Crea y fija el pool del proceso. Tamaños y tope por consulta, de la config."""
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn, min_size=min_size, max_size=max_size, command_timeout=command_timeout
    )
    return _pool


def bind_pool(pool: asyncpg.Pool | None) -> None:
    """Fija (o suelta, con `None`) el pool del proceso. El lifespan lo llama."""
    global _pool
    _pool = pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


def _pool_de(request: Request) -> asyncpg.Pool:
    pool = getattr(request.app.state, "db_pool", None)
    return pool if pool is not None else get_pool()


def _vetar(veto: tuple[int, str, str] | None) -> None:
    if veto is not None:
        raise HTTPException(
            status_code=veto[0],
            detail={"error": veto[1], "detail": veto[2], "status": veto[0]},
        )


async def get_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión sin tenant (`public`)."""
    async with _pool_de(request).acquire() as conn:
        yield conn


async def get_vetted_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con el veto de entidad ya pasado; el consumidor fija el `search_path`.

    Solo se mira el veto cuando la petición trae un tenant válido: las rutas
    públicas llevan el slug en el path, y un slug mal formado conserva el 400 de
    `tenant_schema`. Fail-closed si el registro no responde.
    """
    async with _pool_de(request).acquire() as conn:
        slug = slug_de(request)
        if slug_valido(slug):
            _vetar(await veredicto_entidad(conn, slug))
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


async def get_tenant_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con el veto de entidad pasado y `search_path` al schema del tenant."""
    slug = slug_de(request)
    schema = tenant_schema(slug)
    async with _pool_de(request).acquire() as conn:
        _vetar(await veredicto_entidad(conn, slug))
        await conn.execute(f'SET search_path = "{schema}"')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


async def get_optional_tenant_conn(
    request: Request,
) -> AsyncGenerator[asyncpg.Connection | None, None]:
    """Conexión de tenant si hay slug y su schema tiene `auth_users`; si no, `None`.

    Para rutas que sirven tanto a una persona de una entidad como a un superadmin
    sin entidad (`GET /auth/me`). `None` = sin contexto de tenant: el llamante
    aplica valores por defecto fail-closed. Un schema sin `auth_users` no es un
    error del servidor: es «sin usuario aprovisionado», no un 500.
    """
    slug = slug_de(request)
    if not slug:
        yield None
        return
    schema = tenant_schema(slug)
    async with _pool_de(request).acquire() as conn:
        aprovisionado = await conn.fetchval(
            "SELECT to_regclass(format('%I.auth_users', $1::text)) IS NOT NULL", schema
        )
        if not aprovisionado:
            yield None
            return
        await conn.execute(f'SET search_path = "{schema}"')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def acquire_tenant(slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión propia con `search_path` al tenant, para lo que corre fuera de una petición.

    NUNCA reutilizar la conexión de una dependencia en una tarea que sigue viva
    tras responder: su `finally` la devuelve al pool, y el pool puede dársela a
    otra petición de otro tenant mientras la tarea aún la usa.
    """
    schema = tenant_schema(slug)
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}"')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
