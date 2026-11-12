# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Semilla de la política XAdES por entidad contra Postgres REAL.

Un valor presente GANA sobre el entorno: si la cadena sembrara `"off"`, anularía el
respaldo `FIRMA_PERSONAL_REQUIRE_XADES`. Por eso una entidad nueva nace con JSON null
(«sin decidir») y sin asiento en `admin_audit` para esa clave.

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

from tests.integration.provision import drop_schema, provision_schema

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
        schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
        await provision_schema(c, schema)
        yield c
    finally:
        await drop_schema(c, schema)
        await c.close()


_CLAVE = "firma_personal_require_xades"


@pytest.mark.asyncio
async def test_la_cadena_siembra_null_no_off(conn: asyncpg.Connection) -> None:
    assert await conn.fetchval("SELECT value::text FROM sgd_param_admin WHERE key = $1", _CLAVE) == "null"
    n = await conn.fetchval("SELECT count(*) FROM admin_audit WHERE entity = 'param' AND entity_id = $1", _CLAVE)
    assert n == 0
