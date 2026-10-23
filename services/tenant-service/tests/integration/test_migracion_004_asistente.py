# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Migración global 004 (``tenants.asistente_habilitado``) contra Postgres REAL.

Las dos ramas de la regla «actuales encendidas, nuevas apagadas» y la idempotencia
(una reejecución no vuelve a encender a quien apagó). Nunca toca ``public.tenants``
de la base de desarrollo: se aplica el texto de la migración a un schema desechable
``tenant_itest_*`` con una tabla ``tenants`` mínima (se sustituye ``public`` por el
schema en el SQL).
"""

from __future__ import annotations

import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio

from migrations import runner

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1", reason="carril de integración: RUN_INTEGRATION=1"
)

_RAW = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
_OWNER_DSN = _RAW.replace("postgresql+asyncpg://", "postgresql://")


def _sql_004(schema: str) -> str:
    (ruta,) = [p for p in runner._sql_files(runner.GLOBAL_DIR) if p.name.startswith("004_")]
    return ruta.read_text().replace("public", schema)


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[tuple[asyncpg.Connection, str], None]:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    if host not in {"postgres", "localhost", "127.0.0.1"}:
        raise RuntimeError(f"host {host!r} fuera de la allowlist de integración")
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        await c.execute(f'CREATE SCHEMA "{schema}"')
        await c.execute(f'CREATE TABLE "{schema}".tenants (slug text PRIMARY KEY, name text)')
        await c.execute(f"INSERT INTO \"{schema}\".tenants VALUES ('previa', 'Previa')")
        yield c, schema
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


async def _bandera(c: asyncpg.Connection, schema: str, slug: str) -> bool:
    return await c.fetchval(  # type: ignore[no-any-return]
        f'SELECT asistente_habilitado FROM "{schema}".tenants WHERE slug = $1', slug
    )


@pytest.mark.asyncio
async def test_la_entidad_previa_queda_encendida_y_la_nueva_apagada(ctx) -> None:
    c, schema = ctx
    await c.execute(_sql_004(schema))

    assert await _bandera(c, schema, "previa") is True  # rama 1: existía al migrar
    await c.execute(f"INSERT INTO \"{schema}\".tenants (slug, name) VALUES ('nueva', 'Nueva')")
    assert await _bandera(c, schema, "nueva") is False  # rama 2: nace apagada (opt-in)


@pytest.mark.asyncio
async def test_reejecutar_no_vuelve_a_encender_a_quien_apago(ctx) -> None:
    c, schema = ctx
    await c.execute(_sql_004(schema))
    await c.execute(f"UPDATE \"{schema}\".tenants SET asistente_habilitado = false")
    await c.execute(_sql_004(schema))
    assert await _bandera(c, schema, "previa") is False


@pytest.mark.asyncio
async def test_la_columna_no_admite_nulos(ctx) -> None:
    c, schema = ctx
    await c.execute(_sql_004(schema))
    with pytest.raises(asyncpg.NotNullViolationError):
        await c.execute(f"UPDATE \"{schema}\".tenants SET asistente_habilitado = NULL")
