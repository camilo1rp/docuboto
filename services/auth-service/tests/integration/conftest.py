# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Infraestructura de tests de integración contra Postgres REAL.

Cada prueba recibe un schema de tenant desechable con la cadena única de
migraciones (`services/docuboto/migrations/tenant`, ADR-053) y commits reales.
`public` (rol de aplicación, `audit_log`, `tenants`) lo migra antes el runner
`services/docuboto/migrations/runner.py`: aquí no se aplica nada global.

Gating: ``RUN_INTEGRATION=1`` (y Postgres real alcanzable). Sin ella, estos
tests salen ``skipped`` — el carril unit (``docker compose run --rm --no-deps
auth-service pytest -q``) no se ve afectado.
"""

from __future__ import annotations

import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest_asyncio
from orpycamcp_common import migrations

from auth.core.database import tenant_schema

RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION") == "1"


#: The single migration chain of the repo (ADR-053).
CHAIN = Path(__file__).resolve().parents[4] / "services" / "docuboto" / "migrations"

# Este carril crea PARTICIONES y esquemas, que son actos del rol PROPIETARIO, no
# del de la aplicación. Desde que ambos están separados, conectar con el de la
# aplicación falla con «permission denied for schema public» — y hace bien: ése
# es exactamente el privilegio que se le quitó. Se prefiere la conexión del
# propietario, con la de siempre como respaldo.
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""

# (allowlist fail-closed) este tier crea/destruye schemas — nunca debe correr
# por accidente contra un host que no sea desechable de desarrollo/CI.
_DEFAULT_INTEGRATION_DB_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1"})


def _allowed_hosts() -> frozenset[str]:
    override = os.environ.get("INTEGRATION_DB_HOSTS")
    if override:
        return frozenset(h.strip() for h in override.split(",") if h.strip())
    return _DEFAULT_INTEGRATION_DB_HOSTS


def _require_dsn() -> str:
    if not DSN:
        raise RuntimeError(
            "DATABASE_URL no está definida: este carril exige Postgres real. "
            "Corre con -e RUN_INTEGRATION=1 y Postgres arriba (sin --no-deps)."
        )
    host = urllib.parse.urlparse(DSN).hostname
    allowed = _allowed_hosts()
    if host not in allowed:
        raise RuntimeError(
            f"DATABASE_URL apunta al host {host!r}, fuera de la allowlist de "
            f"integración {sorted(allowed)} — jamás debe correr por accidente "
            "contra una base compartida/staging. Si el host es de verdad "
            "desechable, exporta INTEGRATION_DB_HOSTS con una lista que lo incluya."
        )
    return DSN


@pytest_asyncio.fixture
async def db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión asyncpg REAL y propia (no el pool de la app)."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def tenant_ctx(
    db_conn: asyncpg.Connection,
) -> AsyncGenerator[tuple[str, asyncpg.Connection], None]:
    """Provisiona un schema ``tenant_itest_<hex8>`` desechable con la cadena de
    tenant completa aplicada, y lo destruye al terminar."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    schema = tenant_schema(slug)

    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        await migrations.apply_chain(db_conn, CHAIN / "tenant", schema, log=lambda _linea: None)
        # `apply_chain` fija el `search_path` con `SET LOCAL`, que solo dura la
        # transacción de cada archivo. `SET` (sin LOCAL) persiste para el resto
        # de la sesión de esta conexión dedicada, de modo que las queries que el
        # test haga directamente contra `conn` vean las tablas del schema.
        await db_conn.execute(f'SET search_path = "{schema}"')
        yield slug, db_conn
    finally:
        assert schema.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r}"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await db_conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)
