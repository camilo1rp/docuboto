# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Migraciones 010/011 (política XAdES por entidad) contra Postgres REAL.

La 010 sembró `"off"` y un valor presente GANA sobre el entorno, anulando el
respaldo `FIRMA_PERSONAL_REQUIRE_XADES`. La 010 ahora siembra JSON null y la 011
repara los tenants donde ya corrió la vieja, SIN pisar un "off" que la entidad
eligió por la API (esa deja siempre fila en `admin_audit`).

Esquemas desechables `tenant_itest_*`; nunca toca migraciones globales.
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
_CLAVE = "firma_personal_require_xades"


def _migracion(num: str) -> str:
    (ruta,) = [p for p in runner._sql_files(runner.TENANT_DIR) if p.name.startswith(num)]
    return ruta.read_text()


@pytest_asyncio.fixture
async def conn() -> AsyncGenerator[asyncpg.Connection, None]:
    host = urllib.parse.urlparse(_OWNER_DSN).hostname
    if host not in {"postgres", "localhost", "127.0.0.1"}:
        raise RuntimeError(f"host {host!r} fuera de la allowlist de integración")
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    c = await asyncpg.connect(_OWNER_DSN)
    try:
        await c.execute(f'CREATE SCHEMA "{schema}"')
        await c.execute(f'SET search_path = "{schema}"')
        for ruta in runner._sql_files(runner.TENANT_DIR):
            if ruta.name < "010":
                await c.execute(ruta.read_text())
        yield c
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute("RESET search_path")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


async def _valor(c: asyncpg.Connection) -> object:
    return await c.fetchval("SELECT value::text FROM sgd_param_admin WHERE key = $1", _CLAVE)


@pytest.mark.asyncio
async def test_la_010_siembra_null_no_off(conn: asyncpg.Connection) -> None:
    await conn.execute(_migracion("010"))
    assert await _valor(conn) == "null"


@pytest.mark.asyncio
async def test_la_011_repara_el_off_sembrado_y_deja_asiento(conn: asyncpg.Connection) -> None:
    # Estado de un tenant donde corrió la 010 VIEJA.
    await conn.execute(f"INSERT INTO sgd_param_admin (key, value) VALUES ('{_CLAVE}', '\"off\"')")
    await conn.execute(_migracion("011"))
    assert await _valor(conn) == "null"
    filas = await conn.fetch("SELECT * FROM admin_audit WHERE entity='param' AND entity_id=$1", _CLAVE)
    assert len(filas) == 1 and filas[0]["actor_id"] is None


@pytest.mark.asyncio
async def test_la_011_no_pisa_un_off_elegido_por_la_api(conn: asyncpg.Connection) -> None:
    await conn.execute(f"INSERT INTO sgd_param_admin (key, value) VALUES ('{_CLAVE}', '\"off\"')")
    await conn.execute(
        "INSERT INTO admin_audit (entity, entity_id, action, old_value, new_value, actor_id) "
        f"VALUES ('param', '{_CLAVE}', 'update', '\"todos\"', '\"off\"', gen_random_uuid())"
    )
    await conn.execute(_migracion("011"))
    assert await _valor(conn) == '"off"'


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", ['"todos"', '"clasificados"'])
async def test_la_011_no_toca_otros_valores(conn: asyncpg.Connection, valor: str) -> None:
    await conn.execute(f"INSERT INTO sgd_param_admin (key, value) VALUES ('{_CLAVE}', '{valor}')")
    await conn.execute(_migracion("011"))
    assert await _valor(conn) == valor


@pytest.mark.asyncio
async def test_la_011_es_idempotente(conn: asyncpg.Connection) -> None:
    await conn.execute(f"INSERT INTO sgd_param_admin (key, value) VALUES ('{_CLAVE}', '\"off\"')")
    await conn.execute(_migracion("011"))
    await conn.execute(_migracion("011"))
    assert await _valor(conn) == "null"
    n = await conn.fetchval("SELECT count(*) FROM admin_audit WHERE entity_id=$1", _CLAVE)
    assert n == 1
