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

El schema del tenant se provisiona con la cadena ÚNICA de migraciones (ADR-053,
`services/docuboto/migrations/tenant`), así que trae también las tablas de los
demás servicios. `public` (incluido `public.audit_log` con sus triggers
append-only) lo migra ANTES `services/docuboto/migrations/runner.py`: este
conftest solo lo verifica, nunca emite DDL de seguridad.

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
from orpycamcp_common.migrations import apply_chain

from storage.core.database import tenant_schema

# La cadena única de migraciones (ADR-053).
CHAIN = Path(__file__).resolve().parents[3] / "docuboto" / "migrations"

# DSN "pelado" (mismo cálculo que el runner de migraciones) para conectar con
# `asyncpg.connect` directamente (sin pasar por el pool de la app). Calculado
# perezosamente (no a nivel de módulo con `os.environ[...]`) porque este archivo
# se importa también cuando el carril UNIT recoge `tests/` completo, y ese
# carril corre con `--no-deps` sin garantía de que `DATABASE_URL` exista.
# El carril de integración crea esquemas y tablas: actos del rol PROPIETARIO.
# Con el rol de la aplicación falla —y hace bien, es el privilegio que se le
# quitó—. Se prefiere la conexión del propietario, con la de siempre de respaldo.
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
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


# ---------------------------------------------------------------------------
# `public.audit_log`: lo crea la cadena global; aquí solo se verifica.
# ---------------------------------------------------------------------------

_EXPECTED_AUDIT_TRIGGERS = frozenset(
    {
        "trg_audit_log_immutable",
        "trg_audit_log_no_truncate",
        "trg_audit_log_no_mutation",
        "trg_audit_log_default_no_truncate",
        "trg_audit_log_default_no_mutation",
    }
)

# C-5/C14 (ADR-043): la vista CONTRATO de archive (`550_eliminacion_lotes.sql`) que el gate de
# borrado de storage consulta. La cadena crea la vista REAL, pero poblarla exige una transferencia
# con su constancia de firma inobtenible (y esa tabla es append-only): eso lo prueba archive. Este
# carril prueba el lado de storage del contrato, así que `tenant_ctx` la sustituye por una vista
# con LAS MISMAS columnas sobre una tabla: un test que quiera una constancia vigente inserta en
# `citas_anexo_stub`; uno que quiera la vista AUSENTE (fail-closed) hace
# `DROP VIEW v_anexo_citado_vigente`.
DDL_VISTA_CITAS_STUB = """
CREATE TABLE citas_anexo_stub (
    file_id UUID NOT NULL, excepcion_id UUID NOT NULL DEFAULT gen_random_uuid(),
    transferencia_id UUID NOT NULL DEFAULT gen_random_uuid(),
    rol TEXT NOT NULL DEFAULT 'recibe', ordinal INTEGER NOT NULL DEFAULT 1
);
DROP VIEW v_anexo_citado_vigente;
CREATE VIEW v_anexo_citado_vigente AS
    SELECT file_id, excepcion_id, transferencia_id, rol, ordinal FROM citas_anexo_stub;
"""

_VIEW_COLUMNS_SQL = """
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema = $1 AND table_name = 'v_anexo_citado_vigente'
ORDER BY ordinal_position
"""


@pytest_asyncio.fixture(scope="session")
async def _audit_log_ready() -> None:
    """Verifica que `public.audit_log` (+ triggers append-only) exista antes de
    cualquier test de este carril que ejercite `audit.append`. Nunca lo crea:
    una copia propia del DDL haría que estos tests probaran el trigger de ESTE
    fichero y no el de producción."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        if await conn.fetchval("SELECT to_regclass('public.audit_log')") is None:
            raise RuntimeError(
                "public.audit_log no existe: migra `public` antes de correr este carril "
                "(`python services/docuboto/migrations/runner.py`)."
            )
        rows = await conn.fetch(
            """
            SELECT tgname
            FROM pg_trigger
            WHERE tgrelid = ANY(
                ARRAY['public.audit_log'::regclass, 'public.audit_log_default'::regclass]
            )
              AND NOT tgisinternal
              AND tgenabled <> 'D'
            """
        )
        missing = _EXPECTED_AUDIT_TRIGGERS - {row["tgname"] for row in rows}
        if missing:
            raise RuntimeError(
                "public.audit_log existe pero le faltan triggers append-only "
                f"esperados: {sorted(missing)}. Este conftest de TESTS no repara "
                "DDL de seguridad de producción — corrige el estado de esta base "
                "antes de reintentar."
            )
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def db_conn(_audit_log_ready: None) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión asyncpg REAL y propia (no el pool de la app)."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        yield conn
    finally:
        await conn.close()


async def provision_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
    """Aplica la cadena de tenant a *schema* (ya creado)."""
    assert (CHAIN / "tenant").is_dir(), f"no encuentro la cadena de migraciones en {CHAIN}"
    await apply_chain(conn, CHAIN / "tenant", schema, log=lambda _msg: None)


@pytest_asyncio.fixture
async def tenant_ctx(
    db_conn: asyncpg.Connection, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[str, asyncpg.Connection], None]:
    """Provisiona un schema `tenant_itest_<hex8>` desechable con la cadena única de
    migraciones de tenant aplicada, y lo destruye (`DROP SCHEMA CASCADE`) al
    terminar el test.

    Yields ``(slug, conn)``: ``slug`` es el tenant_slug "crudo" (p. ej.
    ``itest-a1b2c3d4``), el mismo que consume `PreservacionWormRepository(conn, slug)`.
    """
    slug = _make_slug(request.node.name)
    schema = tenant_schema(slug)

    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        await provision_tenant_schema(db_conn, schema)
        contrato = await db_conn.fetch(_VIEW_COLUMNS_SQL, schema)
        async with db_conn.transaction():
            await db_conn.execute(f'SET LOCAL search_path = "{schema}"')
            await db_conn.execute(DDL_VISTA_CITAS_STUB)
        assert contrato and await db_conn.fetch(_VIEW_COLUMNS_SQL, schema) == contrato, (
            "la vista simulada ya no tiene las columnas de la vista contrato real"
        )
        yield slug, db_conn
    finally:
        assert schema.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r} — "
            "no tiene el prefijo tenant_itest_ esperado del schema de un test"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await db_conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)
