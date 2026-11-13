# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Infraestructura de tests de integración contra Postgres REAL (signature-service).

Portado de `document-service/tests/integration/conftest.py` (mismo patrón que
`archive-service` y `storage-service`): conexión `asyncpg` real
contra schemas de tenant desechables, con COMMITS reales y
`DROP SCHEMA ... CASCADE` en el teardown — sin envolver en una tx externa.

Alcance en signature-service: el camino de firma NATIVA — el de cualquier
firmante sin credencial PKI personal, que es el caso común. Ese camino no
tenía una sola prueba contra Postgres: todas las suites doblan la conexión con
`AsyncMock`, y un mock acepta cualquier fila. Por eso pasó inadvertido durante
toda la vida del servicio que `firma.tipo_firma` es NOT NULL y el INSERT le
pasaba NULL explícito —que **ignora el DEFAULT**—: firmar reventaba con
`NotNullViolationError` y un 500 crudo. En la base de demostración no existía
ni una firma `nativa`; el camino nunca había llegado a la base.

Es la clase de defecto que este tier existe para cazar: los que solo aparecen
cuando quien valida es Postgres y no un doble.

El schema del tenant se provisiona con la cadena ÚNICA de migraciones (ADR-053,
`services/docuboto/migrations/tenant`). `public` (incluido `public.audit_log`
con sus triggers append-only) lo migra ANTES
`services/docuboto/migrations/runner.py`: este conftest solo lo verifica.

Gating idéntico: `RUN_INTEGRATION=1` (ver `pytest_collection_modifyitems`).
El carril unit normal (`docker compose run --rm --no-deps signature-service
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
from orpycamcp_common.migrations import apply_chain

from signature.core.database import _schema as tenant_schema

# La cadena única de migraciones (ADR-053).
CHAIN = Path(__file__).resolve().parents[3] / "docuboto" / "migrations"

# DSN "pelado" (mismo cálculo que el runner de migraciones) para conectar con
# `asyncpg.connect` directamente (sin pasar por el pool de la app). Calculado
# perezosamente (no a nivel de módulo con `os.environ[...]`) porque este
# archivo se importa también cuando el carril UNIT recoge `tests/` completo,
# y ese carril corre con `--no-deps` sin garantía de que `DATABASE_URL` exista.
# El carril de integración crea esquemas y tablas: actos del rol PROPIETARIO.
# Con el rol de la aplicación falla —y hace bien, es el privilegio que se le
# quitó—. Se prefiere la conexión del propietario, con la de siempre de respaldo.
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
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


# ---------------------------------------------------------------------------
# Bootstrap de `public.audit_log` — PORTADO desde
# `archive-service/tests/integration/conftest.py`, que es donde se escribió y se
# validó. El docstring de este módulo decía «si este carril crece para cubrir
# rutas que sí auditan, replicar el bootstrap de archive-service»: creció. El
# descarte de un correo escribe en la cadena inmutable y su atomicidad no se
# puede comprobar con un doble — el rollback lo produce Postgres.
#
# Verificar-primero / crear-solo-si-no-existe: si `public.audit_log` ya existe
# (auth-service ya migró esta base compartida) NO se emite ninguna DDL, solo se
# comprueba que los triggers append-only estén ahí y HABILITADOS; un fichero de
# tests jamás repara DDL de seguridad de producción, porque un
# `DROP TRIGGER; CREATE TRIGGER` deja la tabla mutable en la ventana intermedia.
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

@pytest_asyncio.fixture(scope="session")
async def _audit_log_ready() -> None:
    """Verifica que `public.audit_log` (+ triggers append-only) exista antes de
    cualquier test de este carril. Nunca lo crea: una copia propia del DDL haría
    que estos tests probaran el trigger de ESTE fichero y no el de producción."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        if await conn.fetchval("SELECT to_regclass('public.audit_log')") is None:
            raise RuntimeError(
                "public.audit_log no existe: migra `public` antes de correr este carril "
                "(`python services/docuboto/migrations/runner.py`)."
            )
        # (G3) `tgenabled <> 'D'`: un trigger DESHABILITADO (`ALTER TABLE ...
        # DISABLE TRIGGER`, o un `session_replication_role = replica` en la
        # sesión) sigue figurando en `pg_trigger` — sin este filtro, el
        # guardarraíl daba luz verde a un `audit_log` MUTABLE, que es
        # exactamente la condición que existe para descartar.
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


_slug_counter = itertools.count()


def _make_slug(node_name: str) -> str:
    raw = f"{node_name}:{next(_slug_counter)}:{uuid.uuid4()}"
    hexpart = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"itest-{hexpart}"


@pytest_asyncio.fixture
async def db_conn(_audit_log_ready: None) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión asyncpg REAL y propia (no el pool de la app).

    Depende de `_audit_log_ready` para que la cadena inmutable exista antes de
    cualquier test: sin ella, el asiento del descarte fallaría con un error de
    tabla inexistente y el test lo leería como «la atomicidad funciona».
    """
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        yield conn
    finally:
        await conn.close()


async def _provision_tenant_schema(conn: asyncpg.Connection, slug: str) -> str:
    """Crea `tenant_itest_<hex8>` y aplica dentro la cadena de tenant.
    Devuelve el nombre de schema ya entrecomillado-listo (`tenant_schema(slug)`)."""
    assert (CHAIN / "tenant").is_dir(), f"no encuentro la cadena de migraciones en {CHAIN}"
    schema = tenant_schema(slug)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await apply_chain(conn, CHAIN / "tenant", schema, log=lambda _msg: None)
    return schema


async def _drop_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
    assert schema.startswith("tenant_itest_"), (
        f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r} — "
        "no tiene el prefijo tenant_itest_ esperado del schema de un test"
    )
    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)


@pytest_asyncio.fixture
async def tenant_ctx(
    db_conn: asyncpg.Connection, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[str, asyncpg.Connection], None]:
    """Provisiona UN schema `tenant_itest_<hex8>` desechable con la cadena de
    tenant aplicada, y lo destruye
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


# ---------------------------------------------------------------------------
# Filas mínimas VÁLIDAS en las tablas reales de otros servicios que el gate de
# no-read-up lee (`radicados`, `expedientes`). La cadena única las crea en todo
# tenant: ya no hay stubs, así que cada fila tiene que pasar sus CHECK reales.
# ---------------------------------------------------------------------------


async def insert_radicado(conn: asyncpg.Connection, tracking_number: str, nivel: int) -> uuid.UUID:
    """Inserta un radicado real con `nivel_seguridad = nivel` en el search_path actual.

    Un nivel >= 2 exige causal y fundamento (`radicados_causal_catalogo`,
    `radicados_clasificado_con_fundamento`): se usa `por_clasificar`.
    """
    clasificado = nivel >= 2
    return await conn.fetchval(
        """
        INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code,
                               nivel_seguridad, clasificacion_causal, clasificacion_fundamento)
        VALUES ($1, 'E', 2026, $2, 'itest', 'ITEST', $3, $4, $5)
        RETURNING id
        """,
        tracking_number,
        next(_slug_counter),
        nivel,
        "por_clasificar" if clasificado else None,
        "Fundamento de prueba de integración" if clasificado else None,
    )


async def insert_expediente(conn: asyncpg.Connection, code: str, nivel: int) -> uuid.UUID:
    """Inserta un expediente real con `nivel_seguridad = nivel` en el search_path actual."""
    return await conn.fetchval(
        "INSERT INTO expedientes (code, name, nivel_seguridad) VALUES ($1, 'itest', $2) RETURNING id",
        code,
        nivel,
    )
