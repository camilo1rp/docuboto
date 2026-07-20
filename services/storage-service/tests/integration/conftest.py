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

Alcance histórico MÁS ACOTADO que el de archive-service: los primeros tests de
este carril solo ejercitaban el gate del remedio E10 Inc.2
(`PreservacionWormRepository.active_lock_for_expediente`) contra el SQL real, y
no bootstrapeaban `public.audit_log`. `test_aip_concurrent_real_sql.py`
(remediación de auditoría del cableado del AIP, E10-B) SÍ ejercita
`AipService.empaquetar_aip` de punta a punta (incluido `audit.append`), así que
`db_conn` ahora bootstrapea `public.audit_log` (+ triggers append-only) primero
— mismo criterio y DDL que `archive-service/tests/integration/conftest.py`
(sección A2/G3): verificar-primero / crear-solo-si-no-existe, nunca reemitir
DDL de seguridad sobre una tabla que ya existe.

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


# ---------------------------------------------------------------------------
# Bootstrap de `public.audit_log` (mismo DDL/criterio que
# `archive-service/tests/integration/conftest.py`, secciones A2/G3) — este
# carril solo lo necesita desde `test_aip_concurrent_real_sql.py`, que ejercita
# `AipService.empaquetar_aip` de punta a punta (incluye `audit.append`).
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

# Mismo DDL que la migración global de auth-service (002/003/004) contra la
# que `orpycamcp_common.audit` fue diseñado -- copiado, no importado, porque
# este carril no tiene visibilidad del árbol de auth-service (servicios
# independientes, ADR-002).
_AUDIT_LOG_CREATE_DDL = """
CREATE TABLE public.audit_log (
    id           BIGINT      GENERATED ALWAYS AS IDENTITY,
    tenant_slug  TEXT        NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    service      TEXT        NOT NULL,
    actor        TEXT,
    canal        TEXT        NOT NULL DEFAULT 'api',
    action       TEXT        NOT NULL,
    object_type  TEXT        NOT NULL,
    object_ref   TEXT,
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    prev_hash    TEXT,
    hash         TEXT        NOT NULL,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE public.audit_log_default
    PARTITION OF public.audit_log DEFAULT;

CREATE INDEX ix_audit_tenant_ts ON public.audit_log (tenant_slug, ts);
CREATE INDEX ix_audit_object    ON public.audit_log (object_type, object_ref);

ALTER TABLE public.audit_log
    ADD CONSTRAINT chk_audit_log_canal
    CHECK (canal IN ('api', 'ui', 'mcp', 'system'));

CREATE OR REPLACE FUNCTION public.reject_audit_log_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'public.audit_log is append-only (Ley 594/2000, ADR-008)';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON public.audit_log
    FOR EACH ROW EXECUTE FUNCTION public.reject_audit_log_mutation();

CREATE OR REPLACE FUNCTION public.reject_audit_log_truncate()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'TRUNCATE on public.audit_log is prohibited (ADR-008, Ley 594/2000, D-03)';
END;
$$;

CREATE TRIGGER trg_audit_log_no_truncate
    BEFORE TRUNCATE ON public.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_truncate();

CREATE TRIGGER trg_audit_log_default_no_truncate
    BEFORE TRUNCATE ON public.audit_log_default
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_truncate();

CREATE OR REPLACE FUNCTION public.reject_audit_log_mutation_d03()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'UPDATE/DELETE on public.audit_log is prohibited '
        '(ADR-008, Ley 594/2000, D-03): table is append-only';
END;
$$;

CREATE TRIGGER trg_audit_log_no_mutation
    BEFORE UPDATE OR DELETE ON public.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03();

CREATE TRIGGER trg_audit_log_default_no_mutation
    BEFORE UPDATE OR DELETE ON public.audit_log_default
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03();
"""


@pytest_asyncio.fixture(scope="session")
async def _audit_log_ready() -> None:
    """Asegura que `public.audit_log` (+ triggers append-only) exista antes de
    cualquier test de este carril que ejercite `audit.append`.
    Verificar-primero / crear-solo-si-no-existe: jamás re-emite DDL de
    seguridad sobre una tabla que ya existe (mismo criterio que
    archive-service)."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        already_exists = await conn.fetchval("SELECT to_regclass('public.audit_log')")
        if already_exists is not None:
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
            found = {row["tgname"] for row in rows}
            missing = _EXPECTED_AUDIT_TRIGGERS - found
            if missing:
                raise RuntimeError(
                    "public.audit_log ya existe pero le faltan triggers append-only "
                    f"esperados: {sorted(missing)}. Este conftest de TESTS no repara "
                    "DDL de seguridad de producción — corrige el estado de esta base "
                    "antes de reintentar."
                )
            return

        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '5s'")
            await conn.execute(_AUDIT_LOG_CREATE_DDL)
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
