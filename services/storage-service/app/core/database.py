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
from app.core.conservacion import admitir_conservacion

_pool: asyncpg.Pool | None = None

# El slug se valida con `orpycamcp_common.tenant` (única definición, AREA-02-05).


def tenant_schema(slug: str) -> str:
    """Devuelve el identificador de schema `tenant_{slug}` validado (fail-closed 400).

    Normaliza guiones a guiones bajos. Úsese SIEMPRE entrecomillado:
    ``f'SET search_path TO "{tenant_schema(slug)}", public'``.
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
    """Pool del proceso, para quien no tiene `Request` (sondas de salud, jobs)."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


async def get_db_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        # ADR-035 / B9-N3: una entidad no operativa no se atiende en la PUERTA de este
        # servicio, no solo en el gateway. storage-service no tiene `get_tenant_conn`: esta
        # dependencia es el punto común por el que pasa toda petición con tenant, y sin
        # esta defensa un juego de cabeceras de identidad reproducido dentro de la red
        # evadía la suspensión (la caché del gateway da hasta 15 s y hablarle directo al
        # servicio no pasa por él). Solo se mira cuando la petición trae un tenant válido:
        # las rutas públicas llevan el slug en el path y no esta cabecera, y un slug mal
        # formado conserva el 400 de `tenant_schema`. Fail-closed si el registro no responde.
        slug = request.headers.get("X-Tenant-Slug", "")
        if (
            slug_valido(slug)
            and (veto := await veredicto_entidad(conn, slug)) is not None
            # Conservación (ADR-035, enmienda B10): una entidad SUSPENDIDA sigue conservando.
            and not await admitir_conservacion(
                conn,
                slug=slug,
                metodo=request.method,
                ruta=request.scope["path"],
                token=request.headers.get("X-Internal-Token"),
            )
        ):
            raise HTTPException(
                status_code=veto[0],
                detail={"error": veto[1], "detail": veto[2], "status": veto[0]},
            )
        try:
            yield conn
        finally:
            # Defensa en profundidad: la conexión vuelve al pool con search_path
            # neutro, no heredando el schema del último tenant atendido.
            await conn.execute("SET search_path = public")
