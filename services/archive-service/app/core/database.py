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

import asyncpg
from fastapi import HTTPException, Request, status
from orpycamcp_common.tenant import slug_valido, veredicto_entidad
from orpycamcp_common.tenant import tenant_schema as _schema_de

from app.core.config import settings

_pool: asyncpg.Pool | None = None

# El slug se valida con `orpycamcp_common.tenant` (única definición, AREA-02-05).


def tenant_schema(slug: str) -> str:
    """Devuelve el identificador de schema `tenant_{slug}` validado (fail-closed 400).

    Normaliza guiones a guiones bajos. El resultado se usa SIEMPRE entrecomillado:
    ``f'SET search_path TO "{tenant_schema(slug)}"'``.
    """
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
    """Acceso al pool fuera del ciclo de vida de un request (jobs/tareas detached)."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


async def get_db_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        # ADR-035 / B9-N3: una entidad no operativa no se atiende en la PUERTA de este
        # servicio, no solo en el gateway. archive-service no tiene `get_tenant_conn`: esta
        # dependencia es el punto común por el que pasa toda petición con tenant, y sin
        # esta defensa un juego de cabeceras de identidad reproducido dentro de la red
        # evadía la suspensión (la caché del gateway da hasta 15 s y hablarle directo al
        # servicio no pasa por él). Solo se mira cuando la petición trae un tenant válido:
        # las rutas públicas llevan el slug en el path y no esta cabecera, y un slug mal
        # formado conserva el 400 de `tenant_schema`. Fail-closed si el registro no responde.
        slug = request.headers.get("X-Tenant-Slug", "")
        if slug_valido(slug) and (veto := await veredicto_entidad(conn, slug)) is not None:
            raise HTTPException(
                status_code=veto[0],
                detail={"error": veto[1], "detail": veto[2], "status": veto[0]},
            )
        try:
            yield conn
        finally:
            # Defensa en profundidad: la conexión vuelve al pool con search_path
            # neutro (no hereda el schema del último tenant). Cada consumidor lo
            # fija explícitamente vía tenant_schema antes de consultar.
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def acquire_tenant(tenant_slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión propia con search_path al tenant, para tareas detached de un request
    (p.ej. `asyncio.create_task` tras un 202 — ver `routers/batch.py::_run_batch_job`).

    NUNCA reutilizar la conexión request-scoped de `get_db_conn`/`Depends(get_db_conn)`
    en una tarea que sigue viva tras enviar la respuesta: su `finally` resetea
    `search_path` a `public` y devuelve la conexión al pool en cuanto el generador se
    cierra (justo después de responder), y el pool puede reasignarla a otro request
    de OTRO tenant mientras la tarea de fondo aún la usa — contaminación cross-tenant
    y uso concurrente de una misma conexión asyncpg (hallazgo D-0x, batch.py).
    """
    schema = tenant_schema(tenant_slug)
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}"')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
