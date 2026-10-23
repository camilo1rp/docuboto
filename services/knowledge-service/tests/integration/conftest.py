# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Infraestructura de tests de integración contra Postgres REAL (knowledge-service).

Réplica del piloto de `archive-service/tests/integration/conftest.py` (mismo gate,
misma allowlist de host, mismo bootstrap de `public.audit_log`, mismo aislamiento
por schema `tenant_itest_<hex8>` desechable con DROP CASCADE en teardown — ver ese
conftest para el razonamiento completo). Este carril existe específicamente para
validar contra Postgres REAL el fix del hallazgo #1 de tenant-security-auditor: el
candado `GREATEST(...)` de `KnowledgeRepository.upsert_chunk` es una garantía del
MOTOR (UNIQUE (source_type, source_ref) + ON CONFLICT + GREATEST evaluados en el
servidor), no del código Python — un mock no puede ejercitar la carrera real
created→reclassified→created-tardío que motivó el patrón TOMBSTONE
(ver [[tier-integracion-atomicidad]] en memoria de proyecto).

Diferencia con el piloto de archive-service: aquí también hay que garantizar la
extensión `pgvector` en la base (migración GLOBAL `001_pgvector.sql`) antes de
poder aplicar las migraciones de TENANT (`vector(64)` como tipo de columna) — ver
`_pgvector_ready`.

Gating: `RUN_INTEGRATION=1` (ver `pytest_collection_modifyitems`). Sin el gate,
los tests de este directorio salen `skipped`; si se piden EXPLÍCITAMENTE sin el
gate, la sesión falla ruidoso (ver `_selection_is_explicit`).
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import _schema as tenant_schema
from migrations import runner

# Mismo cálculo que `migrations/runner.py:DSN` — DSN "pelado" (driver psycopg-style)
# para conectar con `asyncpg.connect` directamente (sin pasar por el pool de la app).
#
# Deliberadamente NO se usa `os.environ["DATABASE_URL"]` (KeyError) a nivel de
# módulo: este `conftest.py` se importa también cuando el carril UNIT recoge
# `tests/` completo, y ese carril corre con `--no-deps` (sin Postgres arriba, sin
# garantía de que `DATABASE_URL` esté definida). Calcular el DSN aquí sin reventar
# la colección, y fallar ruidoso DENTRO de los fixtures que de verdad lo necesitan.
# El carril de integración crea esquemas y tablas: actos del rol PROPIETARIO.
# Con el rol de la aplicación falla —y hace bien, es el privilegio que se le
# quitó—. Se prefiere la conexión del propietario, con la de siempre de respaldo.
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""

_INTEGRATION_DIR = Path(__file__).parent

# Allowlist fail-closed del HOST del DSN (ver archive-service): este tier crea/
# destruye schemas y escribe filas INDELEBLES en `public.audit_log` (append-only).
_DEFAULT_INTEGRATION_DB_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1"})


def _allowed_integration_hosts() -> frozenset[str]:
    override = os.environ.get("INTEGRATION_DB_HOSTS")
    if override:
        return frozenset(h.strip() for h in override.split(",") if h.strip())
    return _DEFAULT_INTEGRATION_DB_HOSTS


def _require_dsn() -> str:
    """Devuelve el DSN normalizado, o falla ruidoso si falta o apunta fuera de la
    allowlist. Se invoca desde fixtures (tiempo de USO), nunca a nivel de módulo."""
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
            f"integración {sorted(allowed)}: este tier crea/destruye schemas y "
            "escribe filas INDELEBLES en public.audit_log (append-only), así que "
            "jamás debe correr por accidente contra una base compartida/staging. "
            "Si el host es de verdad desechable, exporta INTEGRATION_DB_HOSTS "
            "con una lista separada por comas que lo incluya."
        )
    return DSN


def _in_integration_dir(item: pytest.Item) -> bool:
    try:
        return _INTEGRATION_DIR in Path(item.fspath).parents
    except ValueError:
        return False


_INTEGRATION_TOKEN_RE = re.compile(r"(?<!not\s)\bintegration\b")


def _selection_is_explicit(config: pytest.Config) -> bool:
    markexpr = config.getoption("markexpr", default="") or ""
    if _INTEGRATION_TOKEN_RE.search(markexpr):
        return True
    for raw_arg in config.args:
        arg_path = raw_arg.split("::", 1)[0]
        try:
            resolved = Path(arg_path).resolve()
        except OSError:
            continue
        if resolved == _INTEGRATION_DIR or _INTEGRATION_DIR in resolved.parents:
            return True
    return False


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Gate: sin `RUN_INTEGRATION=1`, marca skip SOLO los tests de este directorio —
    salvo que el operador los haya pedido EXPLÍCITAMENTE, en cuyo caso falla ruidoso.
    (Ver el docstring homólogo en archive-service/tests/integration/conftest.py.)
    """
    if os.environ.get("RUN_INTEGRATION") == "1":
        return

    integration_items = [item for item in items if _in_integration_dir(item)]

    if not integration_items:
        return

    all_selected_are_integration = len(integration_items) == len(items)

    if _selection_is_explicit(config) or all_selected_are_integration:
        pytest.exit(
            "Pediste explícitamente el carril de integración (marker "
            "'integration', ruta tests/integration/, -k/--lf que solo casa "
            "tests de integración, ...) pero falta RUN_INTEGRATION=1 (y un "
            "Postgres real detrás). Corre: docker compose run --rm -e "
            "RUN_INTEGRATION=1 knowledge-service pytest -q -m integration "
            "tests/integration",
            returncode=1,
        )

    skip_integration = pytest.mark.skip(
        reason="integration tests require RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)"
    )
    for item in integration_items:
        item.add_marker(skip_integration)


# ---------------------------------------------------------------------------
# Bootstrap de `public.audit_log` — idéntico al piloto de archive-service:
# verificar-primero, crear-solo-si-no-existe. La DDL la posee auth-service y no
# viaja en la imagen Docker de knowledge-service.
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
    """Asegura `public.audit_log` (+ triggers append-only). Verificar-primero /
    crear-solo-si-no-existe: jamás re-emite DDL de seguridad sobre una tabla que
    ya existe (ver archive-service/tests/integration/conftest.py, mismo patrón)."""
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
                    "DDL de seguridad de producción — corrige las migraciones de "
                    "auth-service (o el estado de esta base) antes de reintentar."
                )
            return

        if os.environ.get("CI"):
            # AREA-03-05: en CI la base nace virgen y el job aplica ANTES las
            # migraciones globales REALES de auth-service (002-008). Crear aquí una
            # copia propia del DDL haría que estos tests probaran el trigger de ESTE
            # fichero y no el de producción: el trigger de sentencia de 004 quedaría
            # sin ejercer. Si llegamos aquí en CI, el job olvidó migrar auth-service.
            raise RuntimeError(
                "public.audit_log no existe en CI: aplica antes las migraciones "
                "globales de auth-service (`python ../auth-service/migrations/runner.py`)."
            )

        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '5s'")
            await conn.execute(_AUDIT_LOG_CREATE_DDL)
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session")
async def _pgvector_ready(_audit_log_ready: None) -> None:
    """Asegura la extensión `pgvector` en la base (migración GLOBAL
    `001_pgvector.sql`) ANTES de aplicar migraciones de tenant, que declaran
    columnas `vector(64)`. `CREATE EXTENSION IF NOT EXISTS` es naturalmente
    idempotente — sin necesidad de la tabla de tracking `public._migrations` del
    runner completo para este propósito acotado."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        for path in runner._sql_files(runner.GLOBAL_DIR):
            await conn.execute(path.read_text())
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Slug único por test (idéntico al piloto de archive-service).
# ---------------------------------------------------------------------------
_slug_counter = itertools.count()


def _make_slug(node_name: str) -> str:
    raw = f"{node_name}:{next(_slug_counter)}:{uuid.uuid4()}"
    hexpart = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"itest-{hexpart}"


@pytest_asyncio.fixture
async def db_conn(_pgvector_ready: None) -> AsyncGenerator[asyncpg.Connection, None]:
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
    migrations de knowledge-service aplicadas (incl. `005` — embedding nullable),
    y lo destruye (`DROP SCHEMA CASCADE`) al terminar el test.

    Yields ``(slug, conn)``: ``slug`` es el tenant_slug "crudo" (p. ej.
    ``itest-a1b2c3d4``), el mismo que consumen `ingestion.py`/`acquire_tenant`.

    A diferencia del piloto de archive-service (donde el SUT fija su propio
    search_path por request), `ingestion.py`/`KnowledgeRepository` documentan
    explícitamente que reciben la conexión con el `search_path` YA fijado (así
    la entrega `acquire_tenant` en producción) — por eso este fixture deja el
    search_path del tenant activo en `conn` de forma persistente (no `LOCAL`,
    que se revertiría al cerrar cada transacción de migración) antes de yield.
    """
    slug = _make_slug(request.node.name)
    schema = tenant_schema(slug)

    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        for path in runner._sql_files(runner.TENANT_DIR):
            async with db_conn.transaction():
                await db_conn.execute(f'SET LOCAL search_path = "{schema}"')
                await db_conn.execute(path.read_text())
        # `radicados` es de document-service y `search`/`get_pivot_chunk` la leen para el
        # gate vivo (B10): sin ella dan 503. Solo las columnas que el gate usa;
        # IF NOT EXISTS para los tests que la declaran ellos mismos.
        await db_conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".radicados ('
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "tracking_number varchar(50) NOT NULL UNIQUE, "
            "nivel_seguridad smallint NOT NULL DEFAULT 1)"
        )
        # Persistente (no LOCAL): las funciones bajo prueba (ingestion.py,
        # KnowledgeRepository) esperan recibir la conexión con el search_path
        # del tenant ya fijado, como lo entrega `acquire_tenant` en producción.
        await db_conn.execute(f'SET search_path = "{schema}"')
        yield slug, db_conn
    finally:
        assert schema.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r} — "
            "no tiene el prefijo tenant_itest_ esperado del schema de un test"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
