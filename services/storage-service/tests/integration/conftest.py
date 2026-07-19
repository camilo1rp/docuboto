# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Infraestructura de tests de integración contra Postgres REAL (storage-service).

Mismo patrón que `archive-service/tests/integration/conftest.py` (piloto): conexión
`asyncpg` real contra un schema de tenant desechable, con COMMITS reales y
`DROP SCHEMA ... CASCADE` en el teardown — sin envolver en una tx externa (el SUT,
`PreservacionWormRepository`/`StorageService`, no abre ninguna tx propia para el
gate de disposición, pero el criterio de aislamiento se mantiene igual: un schema
nuevo por test es más simple de razonar que depender de que cada test recuerde
hacer rollback).

Alcance MÁS ACOTADO que el de archive-service: este carril solo ejercita el gate
del remedio E10 Inc.2 (`PreservacionWormRepository.active_lock_for_expediente`)
contra el SQL real — NO bootstrapea `public.audit_log` porque los tests de este
directorio llaman al repositorio directamente, no a `StorageService.purge_for_
disposition` (que auditaría el bloqueo). Si en el futuro este carril crece para
cubrir `purge_for_disposition` de punta a punta, replicar el bootstrap de
`public.audit_log` de archive-service (ver su conftest, sección A2) en vez de
asumir que la tabla ya existe.

Gating idéntico: `RUN_INTEGRATION=1` (ver `pytest_collection_modifyitems`). El
carril unit normal (`docker compose run --rm --no-deps storage-service pytest -q`)
no tiene Postgres arriba (`--no-deps`); sin el gate, la sola colección de este
directorio intentaría conectar y rompería ese carril.
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

# Mismo cálculo que `migrations/runner.py:33` — DSN "pelado" para conectar con
# `asyncpg.connect` directamente (sin pasar por el pool de la app). Calculado
# perezosamente (no a nivel de módulo con `os.environ[...]`) porque este archivo
# se importa también cuando el carril UNIT recoge `tests/` completo, y ese
# carril corre con `--no-deps` sin garantía de que `DATABASE_URL` exista.
_RAW_DSN = os.environ.get("DATABASE_URL", "")
DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""

_INTEGRATION_DIR = Path(__file__).parent

# Allowlist fail-closed del HOST del DSN (gemelo de archive-service): este tier
# crea/destruye schemas — sin allowlist, un `DATABASE_URL` heredado de staging
# haría que este tier mutara una base que NO es desechable.
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


@pytest_asyncio.fixture
async def tenant_ctx(
    db_conn: asyncpg.Connection, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[str, asyncpg.Connection], None]:
    """Provisiona un schema `tenant_itest_<hex8>` desechable con las tenant
    migrations de storage-service aplicadas, y lo destruye (`DROP SCHEMA CASCADE`)
    al terminar el test.

    Yields ``(slug, conn)``: ``slug`` es el tenant_slug "crudo" (p. ej.
    ``itest-a1b2c3d4``), el mismo que consume `PreservacionWormRepository(conn, slug)`.
    """
    slug = _make_slug(request.node.name)
    schema = tenant_schema(slug)

    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        for path in runner._sql_files(runner.TENANT_DIR):
            async with db_conn.transaction():
                await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await db_conn.execute(path.read_text())
        yield slug, db_conn
    finally:
        assert schema.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r} — "
            "no tiene el prefijo tenant_itest_ esperado del schema de un test"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
