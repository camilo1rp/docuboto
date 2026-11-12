# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""``public.tenants.asistente_habilitado`` contra Postgres REAL.

El asistente es opt-in: una entidad nueva nace con la bandera apagada y la columna
no admite nulos. Todo ocurre dentro de una transacción que se revierte, así que
``public.tenants`` de la base queda como estaba.
"""

from __future__ import annotations

import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1", reason="carril de integración: RUN_INTEGRATION=1"
)

_RAW = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
_OWNER_DSN = _RAW.replace("postgresql+asyncpg://", "postgresql://")


@pytest_asyncio.fixture
async def conn() -> AsyncGenerator[asyncpg.Connection, None]:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    if host not in {"postgres", "localhost", "127.0.0.1"}:
        raise RuntimeError(f"host {host!r} fuera de la allowlist de integración")
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()
    finally:
        await c.close()


async def _alta(c: asyncpg.Connection) -> str:
    slug = f"itest-asis-{uuid.uuid4().hex[:8]}"
    await c.execute(
        "INSERT INTO public.tenants (slug, name, code) VALUES ($1, $2, $3)",
        slug,
        slug,
        f"T{uuid.uuid4().hex[:8]}".upper(),
    )
    return slug


@pytest.mark.asyncio
async def test_la_entidad_nueva_nace_apagada(conn: asyncpg.Connection) -> None:
    slug = await _alta(conn)
    assert (
        await conn.fetchval("SELECT asistente_habilitado FROM public.tenants WHERE slug = $1", slug)
        is False
    )


@pytest.mark.asyncio
async def test_la_columna_no_admite_nulos(conn: asyncpg.Connection) -> None:
    slug = await _alta(conn)
    with pytest.raises(asyncpg.NotNullViolationError):
        await conn.execute(
            "UPDATE public.tenants SET asistente_habilitado = NULL WHERE slug = $1", slug
        )
