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
from typing import TYPE_CHECKING, Any

import asyncpg
from fastapi import HTTPException, Path, Request, status
from orpycamcp_common.tenant import (
    AtencionPublica,
    atencion_publica,
    slug_valido,
    veredicto_entidad,
)
from orpycamcp_common.tenant import tenant_schema as _schema_de

from app.core.config import settings

if TYPE_CHECKING:
    from app.clients.tenant_client import TenantServiceClient

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
    """Conexión sin contexto de tenant (health / registro global)."""
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn


def tenant_schema(slug: str) -> str:
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
    slug = request.headers.get("X-Tenant-Slug", "")
    schema = tenant_schema(slug)
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        # El ESTADO de la entidad se mira en cada petición (ADR-035): suspender
        # una entidad tiene que retirarle el acceso, y un schema inexistente es un
        # 503 limpio, no un `UndefinedTableError` (500). Fail-CLOSED: si el registro
        # no responde, no se atiende.
        if (veto := await veredicto_entidad(conn, slug)) is not None:
            raise HTTPException(
                status_code=veto[0],
                detail={"error": veto[1], "detail": veto[2], "status": veto[0]},
            )
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def acquire_tenant(tenant_slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión PROPIA con `search_path` al tenant, para tareas detached.

    Gemelo exacto de `archive-service/app/core/database.py::acquire_tenant`, que
    ya existía con esta misma advertencia mientras este servicio lanzaba su job
    de lote sobre la conexión del request. El resultado se veía en pantalla: el
    lote se quedaba «Pendiente 0/N» para siempre, porque la tarea moría con
    `InterfaceError: cannot perform operation: another operation is in progress`
    y `asyncio.create_task` no tiene a quién devolverle esa excepción.

    NUNCA reutilizar la conexión request-scoped de `get_tenant_conn` en una
    tarea que sigue viva tras responder: su `finally` resetea `search_path` a
    `public` y devuelve la conexión al pool en cuanto se envía la respuesta, y
    el pool puede reasignarla a un request de OTRO tenant mientras la tarea de
    fondo aún escribe — contaminación cross-tenant además del uso concurrente.
    """
    schema = tenant_schema(tenant_slug)
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


def _no_encontrado() -> HTTPException:
    """404 NEUTRO de las rutas públicas: el mismo cuerpo exacto para una entidad
    inexistente, suspendida o aún sin aprovisionar (no es un oráculo de existencia)."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": "No encontrado", "status": 404},
    )


def _canal_no_disponible() -> HTTPException:
    """503 de las ESCRITURAS públicas de una entidad suspendida (ADR-035).

    No es un 404: la entidad existe y quien radica merece saber que el canal está
    cerrado, no creer que se equivocó de dirección.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "3600"},
        detail={
            "error": "canal_no_disponible",
            "detail": (
                "Esta entidad tiene suspendida la recepción de comunicaciones. "
                "Las consultas y verificaciones siguen disponibles."
            ),
            "status": 503,
        },
    )


@asynccontextmanager
async def public_tenant_conn_atencion(
    pool: asyncpg.Pool, slug: str, *, escritura: bool = False
) -> AsyncGenerator[tuple[asyncpg.Connection, AtencionPublica], None]:
    """Conexión pública que entrega TAMBIÉN la atención.

    Con `escritura=False` (el callback postal, que tiene que autenticar la firma con el
    secreto del schema antes de decidir 503 o 404) la entidad suspendida se sirve. Con
    `escritura=True` una suspendida se corta ANTES de fijar su schema. Inexistente, dada
    de baja y sin aprovisionar son siempre el 404 neutro.
    """
    if not slug_valido(slug):
        raise _no_encontrado()
    async with pool.acquire() as conn:
        try:
            atencion = await atencion_publica(conn, slug)
        except Exception as exc:  # noqa: BLE001 — no poder saberlo se niega
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "tenant_estado_no_disponible",
                    "detail": "No se pudo comprobar el estado de la entidad.",
                    "status": 503,
                },
            ) from exc
        if atencion is AtencionPublica.NINGUNA:
            raise _no_encontrado()
        if escritura and atencion is not AtencionPublica.COMPLETA:
            raise _canal_no_disponible()
        await conn.execute(f'SET search_path = "{_schema_de(slug)}", public')
        try:
            yield conn, atencion
        finally:
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def public_tenant_conn(
    pool: asyncpg.Pool, slug: str, *, escritura: bool = False
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con `search_path` al tenant para una ruta PÚBLICA (sin JWT), ADR-035.

    El slug viaja en el path. Una entidad OPERATIVA se sirve entera; una SUSPENDIDA
    solo en LECTURA (`escritura=True` => 503 `canal_no_disponible`); inexistente, dada
    de baja y sin aprovisionar responden el mismo 404 neutro. Si el registro no
    responde: 503 (fail-closed), que no distingue entidades.
    """
    async with public_tenant_conn_atencion(pool, slug, escritura=escritura) as (conn, _):
        yield conn


async def get_public_tenant_conn(
    request: Request, tenant: str = Path(...)
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Dependencia de las LECTURAS públicas `/public/{tenant}/...` (ver `public_tenant_conn`)."""
    async with public_tenant_conn(request.app.state.db_pool, tenant) as conn:
        yield conn


async def get_public_tenant_conn_escritura(
    request: Request, tenant: str = Path(...)
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Dependencia de las ESCRITURAS públicas (PQRS, ventanilla): cerradas en suspensión."""
    async with public_tenant_conn(request.app.state.db_pool, tenant, escritura=True) as conn:
        yield conn
