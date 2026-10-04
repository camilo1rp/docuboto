# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import HTTPException, Request, status
from orpycamcp_common.tenant import slug_valido
from orpycamcp_common.tenant import tenant_schema as _schema_de

from app.core.config import settings

_pool: asyncpg.Pool | None = None

# El slug se valida con `orpycamcp_common.tenant` (única definición, AREA-02-05).


def _jsonb_encoder(value: object) -> str:
    """Encoder de `jsonb` que NO rompe la convención de escritura del repositorio.

    `KnowledgeRepository` ya serializa con `json.dumps(...)` y castea `$N::jsonb`,
    es decir, bindea un `str` de texto JSON. Si el encoder volviera a aplicar
    `json.dumps` sobre ese `str`, produciría un doble-encode (`'{}'` → `'"{}"'`) y
    corrompería `metadata`/`acl`. Por eso los `str` pasan tal cual (comportamiento
    idéntico al de asyncpg SIN codec); solo se serializa un valor que llegue como
    objeto Python (rama defensiva, hoy ningún call site la usa).
    """
    return value if isinstance(value, str) else json.dumps(value)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Decodifica `jsonb` a dict de Python en toda conexión del pool.

    Sin este codec, asyncpg devuelve las columnas `jsonb` (p. ej. `knowledge_chunk.
    metadata`/`acl`) como el TEXTO crudo `'{}'`, y `SearchHit(**dict(row))` en
    `/search` revienta con `ValidationError` (Pydantic espera un dict). Los tests
    unit no lo detectaban porque mockean la conexión devolviendo dicts de Python; el
    fallo solo aparece contra Postgres real. `/rag` y `/antecedentes` no se veían
    afectados porque no proyectan `metadata` en su respuesta.

    El ENCODE se deja como texto (ver `_jsonb_encoder`) para no alterar la escritura
    ya existente; solo el DECODE cambia (`str` → `dict`).
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=_jsonb_encoder,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


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
        init=_init_connection,
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


def _schema(slug: str) -> str:
    if not slug_valido(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tenant",
                "detail": "X-Tenant-Slug inválido o ausente",
                "status": 400,
            },
        )
    return _schema_de(slug)


async def get_tenant_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    schema = _schema(request.headers.get("X-Tenant-Slug", ""))
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


@asynccontextmanager
async def acquire_tenant(tenant_slug: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con search_path al tenant (worker de ingesta event-driven, E21).

    Espejo de ``workflow-service`` ``acquire_tenant``: el consumer no tiene
    request HTTP, así que resuelve el tenant del sobre del evento (emitido por
    document-service, que ya lo valida en su propio flujo HTTP).
    """
    schema = _schema(tenant_slug)
    async with get_pool().acquire() as conn:
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")
