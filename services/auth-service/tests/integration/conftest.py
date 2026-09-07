# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Infraestructura MÍNIMA de tests de integración contra Postgres REAL
(piloto: validar el backfill SQL de ``migrations/tenant/012_signing_key_
revocacion.sql`` — R3 del Incremento 2). Mismo espíritu que el carril de
``archive-service/tests/integration`` (schema de tenant desechable, commits
reales) pero deliberadamente MÁS LIGERO: este directorio hoy solo ejercita
migraciones SQL crudas, no servicios completos, así que no necesita el
bootstrap de ``public.audit_log`` ni la maquinaria anti-falso-verde de
selección explícita — un ``skipif`` basta.

Gating: ``RUN_INTEGRATION=1`` (y Postgres real alcanzable). Sin ella, estos
tests salen ``skipped`` — el carril unit (``docker compose run --rm --no-deps
auth-service pytest -q``) no se ve afectado.
"""

from __future__ import annotations

import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest_asyncio

from app.core.database import tenant_schema
from migrations import runner

RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION") == "1"

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
    """Provisiona un schema ``tenant_itest_<hex8>`` desechable con TODAS las
    tenant migrations de auth-service aplicadas en orden (001..012 — 011/012
    dependen de ``auth_users``, creada en 001), y lo destruye al terminar."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    schema = tenant_schema(slug)

    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        for path in runner._sql_files(runner.TENANT_DIR):
            async with db_conn.transaction():
                await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await db_conn.execute(path.read_text())
        # `SET LOCAL` (arriba) solo dura la transacción de cada archivo — sin
        # esto, al terminar el loop el `search_path` de la CONEXIÓN vuelve al
        # default y las queries que el test haga directamente contra `conn`
        # (fuera de una tx propia) no verían las tablas del schema recién
        # provisionado. `SET` (sin LOCAL) persiste para el resto de la sesión
        # de esta conexión dedicada.
        await db_conn.execute(f'SET search_path = "{schema}", public')
        yield slug, db_conn
    finally:
        assert schema.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r}"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
