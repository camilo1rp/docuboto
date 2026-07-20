# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Infraestructura de tests de integración contra Postgres REAL (document-service).

Mismo patrón que `archive-service/tests/integration/conftest.py` y
`storage-service/tests/integration/conftest.py`: conexión `asyncpg` real
contra schemas de tenant desechables, con COMMITS reales y
`DROP SCHEMA ... CASCADE` en el teardown — sin envolver en una tx externa.

Alcance de este piloto: SOLO el aislamiento físico por schema del inventario
TOTAL de anexos (`DocumentRepository.list_anexos_for_radicados_internal`,
E10 debt, cableado del AIP al cierre del expediente) — no bootstrapea
`public.audit_log` porque ese repositorio no audita (es una lectura pura,
sin mutación). Si este carril crece para cubrir rutas que sí auditan,
replicar el bootstrap de `public.audit_log` de archive-service.

Gating idéntico: `RUN_INTEGRATION=1` (ver `pytest_collection_modifyitems`).
El carril unit normal (`docker compose run --rm --no-deps document-service
pytest -q`) no tiene Postgres arriba (`--no-deps`); sin el gate, la sola
colección de este directorio intentaría conectar y rompería ese carril.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import tenant_schema
from migrations import runner

# Mismo cálculo que `migrations/runner.py` — DSN "pelado" para conectar con
# `asyncpg.connect` directamente (sin pasar por el pool de la app). Calculado
# perezosamente (no a nivel de módulo con `os.environ[...]`) porque este
# archivo se importa también cuando el carril UNIT recoge `tests/` completo,
# y ese carril corre con `--no-deps` sin garantía de que `DATABASE_URL` exista.
_RAW_DSN = os.environ.get("DATABASE_URL", "")
DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""

_INTEGRATION_DIR = Path(__file__).parent

# Allowlist fail-closed del HOST del DSN (mismo criterio que archive-service/
# storage-service): este tier crea/destruye schemas — sin allowlist, un
# `DATABASE_URL` heredado de staging haría que este tier mutara una base que
# NO es desechable.
_DEFAULT_INTEGRATION_DB_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1"})


def _allowed_integration_hosts() -> frozenset[str]:
    override = os.environ.get("INTEGRATION_DB_HOSTS")
    if override:
        return frozenset(h.strip() for h in override.split(",") if h.strip())
    return _DEFAULT_INTEGRATION_DB_HOSTS


def _require_dsn() -> str:
    if not DSN:
        raise RuntimeError(
            "DATABASE_URL no está definida: el carril de integración exige Postgres "
            "real (ver CLAUDE.md). Si pediste este carril explícitamente, revisa que "
            "no sigas corriendo con --no-deps y que hayas pasado -e RUN_INTEGRATION=1."
        )
    host = urllib.parse.urlparse(DSN).hostname
    allowed = _allowed_integration_hosts()
    if host not in allowed:
        raise RuntimeError(
            f"DATABASE_URL apunta al host {host!r}, fuera de la allowlist de "
            f"integración {sorted(allowed)}: este tier crea/destruye schemas, así "
            "que jamás debe correr por accidente contra una base compartida/staging. "
            "Si el host es de verdad desechable, exporta INTEGRATION_DB_HOSTS "
            "con una lista separada por comas que lo incluya."
        )
    return DSN


def _in_integration_dir(item: pytest.Item) -> bool:
    try:
        return _INTEGRATION_DIR in Path(item.fspath).parents
    except ValueError:
        return False


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Gate: sin `RUN_INTEGRATION=1`, marca skip SOLO los tests de este directorio."""
    if os.environ.get("RUN_INTEGRATION") == "1":
        return

    integration_items = [item for item in items if _in_integration_dir(item)]
    if not integration_items:
        return

    skip_integration = pytest.mark.skip(
        reason="integration tests require RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)"
    )
    for item in integration_items:
        item.add_marker(skip_integration)


_slug_counter = itertools.count()


def _make_slug(node_name: str) -> str:
    raw = f"{node_name}:{next(_slug_counter)}:{uuid.uuid4()}"
    hexpart = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"itest-{hexpart}"


@pytest_asyncio.fixture
async def db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión asyncpg REAL y propia (no el pool de la app)."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        yield conn
    finally:
        await conn.close()


async def _provision_tenant_schema(conn: asyncpg.Connection, slug: str) -> str:
    """Crea `tenant_itest_<hex8>` y aplica las tenant migrations de
    document-service dentro de él. Devuelve el nombre de schema ya
    entrecomillado-listo (`tenant_schema(slug)`)."""
    schema = tenant_schema(slug)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    for path in runner._sql_files(runner.TENANT_DIR):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await conn.execute(path.read_text())
    return schema


async def _drop_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
    assert schema.startswith("tenant_itest_"), (
        f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r} — "
        "no tiene el prefijo tenant_itest_ esperado del schema de un test"
    )
    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest_asyncio.fixture
async def tenant_ctx(
    db_conn: asyncpg.Connection, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[str, asyncpg.Connection], None]:
    """Provisiona UN schema `tenant_itest_<hex8>` desechable con las tenant
    migrations de document-service aplicadas, y lo destruye
    (`DROP SCHEMA CASCADE`) al terminar el test.

    Yields ``(slug, conn)``: ``slug`` es el tenant_slug "crudo" (p. ej.
    ``itest-a1b2c3d4``), el mismo que consume `DocumentRepository(conn, slug)`.
    """
    slug = _make_slug(request.node.name)
    schema = await _provision_tenant_schema(db_conn, slug)
    try:
        yield slug, db_conn
    finally:
        await _drop_tenant_schema(db_conn, schema)


@pytest_asyncio.fixture
async def two_tenant_ctx(
    db_conn: asyncpg.Connection, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[str, str, asyncpg.Connection], None]:
    """Provisiona DOS schemas `tenant_itest_<hex8>` desechables (uno por
    tenant) sobre la MISMA conexión — necesario para probar aislamiento
    físico cruzado (un `radicado_id`/anexo de un tenant nunca visible desde
    el `search_path` del otro) dentro de un ÚNICO test. Ambos se destruyen
    al terminar, incluso si el test falla a mitad de camino."""
    slug_a = _make_slug(f"{request.node.name}-a")
    slug_b = _make_slug(f"{request.node.name}-b")
    schema_a = await _provision_tenant_schema(db_conn, slug_a)
    schema_b = await _provision_tenant_schema(db_conn, slug_b)
    try:
        yield slug_a, slug_b, db_conn
    finally:
        await _drop_tenant_schema(db_conn, schema_a)
        await _drop_tenant_schema(db_conn, schema_b)
