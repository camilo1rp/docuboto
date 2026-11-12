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

Cada schema de prueba recibe la cadena ÚNICA de migraciones de tenant
(`services/docuboto/migrations/tenant`, ADR-053): las tablas de todos los
servicios, tal como salen a producción. `public` (con `public.audit_log`, la
cadena inmutable que escriben las rutas que auditan) lo migra ANTES el runner
único, `services/docuboto/migrations/runner.py`; este conftest solo comprueba
que esté.

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
from orpycamcp_common import migrations

from document.core.database import tenant_schema

# Mismo cálculo que `services/docuboto/migrations/runner.py` — DSN "pelado" para conectar con
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

# La cadena única de migraciones (ADR-053): `services/docuboto/migrations`.
CHAIN = Path(__file__).resolve().parents[3] / "docuboto" / "migrations"

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
# `public.audit_log` — lo crea el runner único, nunca este fichero. Aquí solo se
# comprueba que exista y que sus triggers append-only estén ahí y HABILITADOS:
# un fichero de tests jamás emite ni repara DDL de seguridad de producción, y
# una copia propia haría que estas pruebas ejercieran el trigger de ESTE fichero
# y no el que sale a producción.
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
    """Comprueba que `public.audit_log` (+ triggers append-only) exista antes de
    cualquier test de este carril. No emite DDL: si falta, falla diciendo cómo
    migrar `public`."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        if await conn.fetchval("SELECT to_regclass('public.audit_log')") is None:
            raise RuntimeError(
                "public.audit_log no existe: `public` no está migrado. Ejecuta antes "
                "el runner único (`python services/docuboto/migrations/runner.py`) "
                "contra esta base."
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
        found = {row["tgname"] for row in rows}
        missing = _EXPECTED_AUDIT_TRIGGERS - found
        if missing:
            raise RuntimeError(
                "public.audit_log existe pero le faltan triggers append-only "
                f"esperados: {sorted(missing)}. Este conftest de TESTS no repara "
                "DDL de seguridad de producción — vuelve a ejecutar "
                "`services/docuboto/migrations/runner.py` sobre una base limpia."
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


# Lo que la cadena NO siembra y casi toda prueba necesita: la dependencia 1 (la
# que usan los radicados de prueba desde que Ac. AGN 060/2001 §5.2 la hace
# obligatoria, AREA-09-02) y los tipos documentales ACTIVOS del catálogo que
# exige `DocumentService.create` (B6). Los de ventanilla/publicación (PQRS,
# OFICIO, DEM-PET) los siembra cada prueba, porque su bandera
# `admite_ventanilla_publica` es parte de ella.
_SEED_SQL = """
INSERT INTO dependencias (codigo, sigla, nombre) VALUES (1, 'DG', 'Dirección General');
INSERT INTO tipos_documentales (code, nombre) VALUES
    ('Petición', 'Petición'), ('Oficio', 'Oficio'), ('Memorando', 'Memorando'),
    ('TPLM', 'TPLM'), ('TPL', 'TPL'), ('Derechos de Petición', 'Derechos de Petición');
"""


async def _provision_tenant_schema(conn: asyncpg.Connection, slug: str) -> str:
    """Crea `tenant_itest_<hex8>` y le aplica la cadena única de tenant
    (`apply_chain`, la misma función que usan el runner e `init_tenant`).
    Devuelve el nombre de schema (`tenant_schema(slug)`)."""
    schema = tenant_schema(slug)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await migrations.apply_chain(conn, CHAIN / "tenant", schema, log=lambda _linea: None)
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}"')
        await conn.execute(_SEED_SQL)
    return schema


async def membership(
    conn: asyncpg.Connection, user_id: uuid.UUID, depe_id: int, *, is_active: bool = True
) -> None:
    """Pone a *user_id* en la dependencia *depe_id* (`urd_usuaroldep`).

    La tabla real exige que el usuario exista y un rol: se crean si faltan.
    Idempotente, como el `WHERE NOT EXISTS` al que sustituye.
    """
    await conn.execute(
        "INSERT INTO auth_users (id, username) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
        user_id,
        f"itest-{user_id}",
    )
    rol = await conn.fetchval(
        "INSERT INTO auth_groups (name) VALUES ('itest-rol') "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
    )
    await conn.execute(
        "INSERT INTO urd_usuaroldep (user_id, depe_id, rol_id, is_active) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, depe_id) DO NOTHING",
        user_id,
        depe_id,
        rol,
        is_active,
    )


async def _drop_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
    assert schema.startswith("tenant_itest_"), (
        f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r} — "
        "no tiene el prefijo tenant_itest_ esperado del schema de un test"
    )
    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    # El libro es de `public`: sin esto quedaría una fila por archivo y por test.
    await conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)


@pytest_asyncio.fixture
async def tenant_ctx(
    db_conn: asyncpg.Connection, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[str, asyncpg.Connection], None]:
    """Provisiona UN schema `tenant_itest_<hex8>` desechable con la cadena
    única de tenant aplicada, y lo destruye
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


@pytest_asyncio.fixture
async def tenant_flujo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> tuple[str, asyncpg.Connection]:
    """`tenant_ctx` con el `search_path` de la sesión ya puesto en el schema.
    Las tablas de flujo que lee la tenencia de un borrador (`flow_steps`,
    `urd_usuaroldep`, `dependencias`) las trae la cadena única."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, conn


@pytest.fixture(autouse=True)
def _dependencia_desde_el_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DocumentService._resolve_dependencia` habla con tenant-service por HTTP; en este
    tier no hay tenant-service. Se sustituye por la MISMA regla leída de la tabla
    `dependencias` del schema de prueba (existe y activa), no por un «todo vale»."""
    from document.services.document_service import DocumentService, InvalidDependenciaError

    async def _resolver(self: DocumentService, codigo: int) -> str:
        nombre = await self._conn.fetchval(
            "SELECT nombre FROM dependencias WHERE codigo = $1 AND activo", codigo
        )
        if nombre is None:
            raise InvalidDependenciaError(f"La dependencia {codigo} no existe")
        return str(nombre)

    monkeypatch.setattr(DocumentService, "_resolve_dependencia", _resolver)
