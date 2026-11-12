# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Infraestructura de tests de integración contra Postgres REAL (workflow-service, ADR-028).

Carril DISTINTO del `tests/conftest.py` mockeado: aquí no hay `dependency_overrides`
ni `httpx`/ASGI — se conduce el service/repository con una conexión `asyncpg` real
contra un schema de tenant desechable, con COMMITS reales y `DROP SCHEMA ... CASCADE`
en el teardown.

Por qué NO "envolver cada test en una tx y hacer rollback": el SUT
(`WorkflowService`) abre su propia `conn.transaction()`. Si el test
envolviera la conexión en una tx externa, esa tx interna del SUT degradaría a un
SAVEPOINT y el rollback final del test ocultaría un SUT que hubiera olvidado envolver
su propia operación — falso verde. Aislamos con un schema `tenant_itest_<hex8>` nuevo
por test en su lugar; las filas huérfanas que queden en `public.audit_log` (global,
append-only, no se puede limpiar) son inertes porque cada test usa un `tenant_slug`
único.

Gating: estos tests SOLO corren con `RUN_INTEGRATION=1` (ver `pytest_collection_modifyitems`
más abajo). El carril unit normal (`docker compose run --rm --no-deps archive-service
pytest -q`) no tiene Postgres arriba (`--no-deps`); sin el gate, la sola colección de
este directorio intentaría conectar y rompería ese carril. Con `RUN_INTEGRATION=1` pero
Postgres inalcanzable, deliberadamente NO se hace skip: el operador pidió integración y
está mal configurado, así que debe fallar ruidoso en vez de dar un falso verde. Si en
cambio el operador PIDIÓ explícitamente este carril (`-m integration` o una ruta dentro
de este directorio) y ni siquiera puso `RUN_INTEGRATION=1`, tampoco hay skip silencioso:
es un error de uso ruidoso (ver `_selection_is_explicit`).
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import urllib.parse
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from orpycamcp_common import migrations

from workflow.core.database import tenant_schema

# La cadena única de migraciones (ADR-053): `global/` -> public, `tenant/` -> tenant_*.
CHAIN = Path(__file__).resolve().parents[4] / "services" / "docuboto" / "migrations"

# Mismo cálculo que `services/docuboto/migrations/runner.py` — DSN "pelado" (driver psycopg-style)
# para conectar con `asyncpg.connect` directamente (sin pasar por el pool de la app).
#
# (A4) Deliberadamente NO se usa `os.environ["DATABASE_URL"]` (KeyError) a nivel de
# módulo: este `conftest.py` se importa también cuando el carril UNIT recoge
# `tests/` completo (sin filtrar por directorio), y ese carril corre con
# `--no-deps` — sin Postgres arriba y sin garantía de que `DATABASE_URL` esté
# definida en el entorno. Calcular el DSN aquí sin reventar la colección, y
# fallar ruidoso — pero DENTRO de los fixtures que de verdad lo necesitan
# (`_audit_log_ready`, `db_conn`) — mantiene el carril unit intacto.
# El carril de integración crea esquemas y tablas: actos del rol PROPIETARIO.
# Con el rol de la aplicación falla —y hace bien, es el privilegio que se le
# quitó—. Se prefiere la conexión del propietario, con la de siempre de respaldo.
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""

_INTEGRATION_DIR = Path(__file__).parent

# (G4) Allowlist fail-closed del HOST del DSN: este tier crea/destruye schemas
# y escribe filas INDELEBLES en `public.audit_log` (append-only por diseño —
# el mismo razonamiento que ya justificó quitar el `CREATE ROLE` de este
# conftest, ver (A1)/(A2) más abajo). Sin allowlist, un `DATABASE_URL`
# heredado del shell o de un `.env` de staging haría que este tier mutara una
# base que NO es desechable. Los hosts de docker-compose/dev local están
# cubiertos por defecto; `INTEGRATION_DB_HOSTS` (lista separada por comas)
# permite ampliar la allowlist en despliegues de CI distintos, a propósito.
_DEFAULT_INTEGRATION_DB_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1"})


def _allowed_integration_hosts() -> frozenset[str]:
    override = os.environ.get("INTEGRATION_DB_HOSTS")
    if override:
        return frozenset(h.strip() for h in override.split(",") if h.strip())
    return _DEFAULT_INTEGRATION_DB_HOSTS


def _require_dsn() -> str:
    """Devuelve el DSN ya normalizado, o falla ruidoso si `DATABASE_URL` falta
    o apunta a un host fuera de la allowlist de integración (G4).

    Se invoca desde fixtures (tiempo de USO), nunca desde el cuerpo del módulo
    (tiempo de COLECCIÓN) — ver (A4) más arriba.
    """
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


# (G1) Detecta el token `integration` en un `markexpr` SOLO cuando no está
# negado por un `not ` inmediatamente delante. El idioma CANÓNICO de CI para
# EXCLUIR este carril es justo `-m "not integration"` — antes de este fix, un
# simple `"integration" in markexpr` (match de SUBSTRING) clasificaba esa
# exclusión como selección EXPLÍCITA y, sin `RUN_INTEGRATION=1`, apagaba la
# sesión entera con `pytest.exit()` — un gate anti-falso-verde convertido en
# apaga-suite para el carril unit. `(?<!not\s)` es un lookbehind de ancho fijo
# (4 caracteres: "not" + un espacio) — válido en `re` de la stdlib, sin deps
# nuevas. No pretende parsear la gramática completa de `-m` (paréntesis,
# `or`/`and` arbitrarios): cubre el idioma real que usa este repo
# ("integration", "not integration", "integration and not X", ...).
_INTEGRATION_TOKEN_RE = re.compile(r"(?<!not\s)\bintegration\b")


def _selection_is_explicit(config: pytest.Config) -> bool:
    """True si el operador pidió ESTE carril a propósito (no de pasada).

    Dos formas de pedirlo explícitamente: `-m integration` (o cualquier
    expresión de marker que MENCIONE `integration` sin negarlo, ver G1), o una
    ruta de invocación que caiga dentro de `tests/integration/` (el propio
    directorio, o un fichero/subdirectorio suyo). En cualquiera de los dos
    casos, si falta `RUN_INTEGRATION=1` el resultado correcto es un error de
    uso ruidoso, NO un "0 tests ran" / "N skipped" con exit code 0 (ver A3).
    """
    markexpr = config.getoption("markexpr", default="") or ""
    if _INTEGRATION_TOKEN_RE.search(markexpr):
        return True
    for raw_arg in config.args:
        # `config.args` trae los argumentos de invocación tal cual (pueden
        # incluir sufijos `::test_nombre` de selección de nodo) — se pela el
        # sufijo antes de resolver la ruta en disco.
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

    Este hook lo invoca pytest para la colección COMPLETA de la sesión (no solo los
    tests de `tests/integration/`), así que hay que filtrar explícitamente por
    ubicación — de lo contrario, correr `pytest -q` desde la raíz de `tests/`
    saltaría también el carril unit mockeado cuando falte la env var.

    (G2) `trylast=True` NO es decorativo: los hooks de conftest se invocan ANTES
    que los de los plugins internos, así que sin él `items` llegaba aquí con la
    colección ENTERA — la deselección de `-k`/`-m`/`--lf` la aplica el plugin
    `mark` en su propio `pytest_collection_modifyitems`, que corría DESPUÉS.
    Con la colección entera, `all_selected_are_integration` era siempre falso y
    `pytest -k <nombre_de_test_de_integración>` colaba como "de pasada":
    "1 skipped, EXIT 0" — un falso verde por la puerta de atrás (el operador
    apuntó por nombre a un test de integración y se llevó una sesión verde sin
    ejecutar nada). Con `trylast` vemos la selección YA cribada, y entonces "todo
    lo que sobrevivió es de este directorio" sí significa "lo pidió a propósito",
    sea cual sea el mecanismo — incluidos los que no pasan por `markexpr` ni por
    `config.args`.
    """
    if os.environ.get("RUN_INTEGRATION") == "1":
        return

    integration_items = [item for item in items if _in_integration_dir(item)]

    if not integration_items:
        return

    all_selected_are_integration = len(integration_items) == len(items)

    if _selection_is_explicit(config) or all_selected_are_integration:
        # (A3) Pedido explícito + gate ausente: NO es un "skip de pasada", es
        # un operador que quería este carril y se le olvidó la env var (o
        # sigue con --no-deps). Dar 0 fallos/"N skipped" aquí sería un falso
        # verde del carril que el operador SÍ pidió — se corta la sesión.
        pytest.exit(
            "Pediste explícitamente el carril de integración (marker "
            "'integration', ruta tests/integration/, -k/--lf que solo casa "
            "tests de integración, ...) pero falta RUN_INTEGRATION=1 (y un "
            "Postgres real detrás). Corre: docker compose run --rm -e "
            "RUN_INTEGRATION=1 workflow-service pytest -q -m integration "
            "tests/integration",
            returncode=1,
        )

    skip_integration = pytest.mark.skip(
        reason="integration tests require RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)"
    )
    for item in integration_items:
        item.add_marker(skip_integration)


# ---------------------------------------------------------------------------
# `public.audit_log`: se VERIFICA, nunca se crea aquí.
#
# `public` lo migra la cadena única (`services/docuboto/migrations/global`,
# ADR-053) ANTES de los tests. Este fichero de TESTS no emite DDL de seguridad
# sobre la base de `DATABASE_URL`: comprueba que la tabla y sus triggers
# append-only estén presentes y habilitados, y si falta algo falla ruidoso.
# Beneficio colateral: los tests de este tier ejercitan los triggers REALES de
# producción, no unos que el propio conftest instaló.
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

_RUNNER_HINT = (
    "Aplica antes la cadena única de migraciones: "
    "`python services/docuboto/migrations/runner.py` (con MIGRATIONS_DATABASE_URL "
    "o DATABASE_URL apuntando a esta base)."
)


@pytest_asyncio.fixture(scope="session")
async def _audit_log_ready() -> None:
    """Verifica que `public.audit_log` (+ triggers append-only) exista antes de
    cualquier test de este carril. Jamás emite DDL: `public` es del runner."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        if await conn.fetchval("SELECT to_regclass('public.audit_log')") is None:
            raise RuntimeError(f"public.audit_log no existe en esta base. {_RUNNER_HINT}")
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
                f"DDL de seguridad de producción. {_RUNNER_HINT}"
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Slug único por test: hash de nodeid + contador de módulo + uuid4.
#
# Primer intento (descartado): nodeid + contador de módulo, SIN uuid4/random,
# para mantener la generación trazable/determinista dentro de una sesión. Se
# rompió en la práctica: el contador de módulo se reinicia a 0 en cada proceso
# `pytest` nuevo (cada `docker compose run` es un proceso fresco) y `node_name`
# es estable entre corridas → dos invocaciones SEPARADAS de
# `pytest -m integration tests/integration` producían el MISMO slug, y por
# tanto el mismo `tenant_slug` en el `audit_log` global (que nunca se limpia):
# la segunda corrida veía 2 filas donde esperaba 1 (colisión confirmada al
# validar esta infra dos veces seguidas). `uuid.uuid4()` ya se usa sin reparos
# en `tests/conftest.py` (`USER_ID`, `EXPEDIENTE_ID`, ...) — no hay ninguna
# restricción de entorno contra él en este proyecto — así que se incorpora
# aquí para garantizar unicidad real entre procesos; el nodeid+contador se
# conserva en la semilla solo por trazabilidad, no aporta la unicidad.
# ---------------------------------------------------------------------------
_slug_counter = itertools.count()


def _make_slug(node_name: str) -> str:
    raw = f"{node_name}:{next(_slug_counter)}:{uuid.uuid4()}"
    hexpart = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    # Encaja en `_SLUG_RE` (`^[a-z0-9][a-z0-9-]{0,54}$`) y en el límite de 63 bytes
    # de identificador de Postgres una vez prefijado `tenant_` por `tenant_schema`.
    return f"itest-{hexpart}"


@pytest_asyncio.fixture
async def db_conn(_audit_log_ready: None) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión asyncpg REAL y propia (no el pool de la app). El SUT
    (`WorkflowService`) fija su propio `search_path` vía `_set_search_path`, así que
    esta conexión solo necesita existir y estar limpia."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def nueva_conexion(
    _audit_log_ready: None,
) -> AsyncGenerator[Callable[[], Awaitable[asyncpg.Connection]], None]:
    """Fábrica de conexiones asyncpg ADICIONALES (para carreras entre sesiones
    reales y para observar desde fuera lo que otra sesión ya commiteó). Se
    cierran todas al terminar el test."""
    dsn = _require_dsn()
    abiertas: list[asyncpg.Connection] = []

    async def _abrir() -> asyncpg.Connection:
        c = await asyncpg.connect(dsn=dsn)
        abiertas.append(c)
        return c

    try:
        yield _abrir
    finally:
        for c in abiertas:
            await c.close()


async def provision_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
    """Crea *schema* y le aplica la cadena de tenant COMPLETA (ADR-053), la misma
    que corre en producción."""
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await migrations.apply_chain(conn, CHAIN / "tenant", schema, log=lambda _msg: None)


async def drop_tenant_schema(conn: asyncpg.Connection, schema: str) -> None:
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
    """Provisiona un schema `tenant_itest_<hex8>` desechable con la cadena de
    tenant REAL y completa (`services/docuboto/migrations/tenant`), y lo destruye
    (`DROP SCHEMA CASCADE`) al terminar.

    Yields ``(slug, conn)`` con el slug "crudo" que consume `WorkflowService`.
    """
    slug = _make_slug(request.node.name)
    schema = tenant_schema(slug)
    try:
        await provision_tenant_schema(db_conn, schema)
        yield slug, db_conn
    finally:
        await drop_tenant_schema(db_conn, schema)


# ---------------------------------------------------------------------------
# Filas mínimas VÁLIDAS para el esquema real. Las tablas ya no son stubs: cada
# ayuda rellena lo que el esquema exige (NOT NULL, FK, CHECK) y nada más. Todas
# asumen que el `search_path` de la conexión es el schema del tenant.
# ---------------------------------------------------------------------------

_row_counter = itertools.count(1)

# Lo que `radicados` exige a un radicado clasificado (CHECK `radicados_causal_catalogo`
# y `radicados_clasificado_con_fundamento`).
_CAUSAL_BY_LEVEL = {1: None, 2: "por_clasificar", 3: "art18_a_intimidad"}


async def insert_radicado(
    conn: asyncpg.Connection,
    tracking_number: str,
    *,
    status: str = "registered",
    nivel: int = 1,
    doc_type: str = "E",
    responde_a: uuid.UUID | None = None,
) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code, "
        "status, nivel_seguridad, clasificacion_causal, clasificacion_fundamento, responde_a) "
        "VALUES ($1, $2, 2026, $3, 'Asunto de prueba', 'ITEST', $4, $5, $6, $7, $8) RETURNING id",
        tracking_number,
        doc_type,
        next(_row_counter),
        status,
        nivel,
        _CAUSAL_BY_LEVEL[nivel],
        "Fundamento de prueba" if nivel >= 2 else None,
        responde_a,
    )


async def set_security_level(conn: asyncpg.Connection, radicado_id: uuid.UUID, nivel: int) -> None:
    """Sube el nivel de un radicado con la causal y el fundamento que el esquema exige."""
    await conn.execute(
        "UPDATE radicados SET nivel_seguridad = $2, clasificacion_causal = $3, "
        "clasificacion_fundamento = $4 WHERE id = $1",
        radicado_id,
        nivel,
        _CAUSAL_BY_LEVEL[nivel],
        "Fundamento de prueba" if nivel >= 2 else None,
    )


async def insert_user(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO auth_users (username) VALUES ($1) RETURNING id", f"itest-{uuid.uuid4().hex[:12]}"
    )


async def insert_group(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO auth_groups (name) VALUES ($1) RETURNING id", f"itest-{uuid.uuid4().hex[:12]}"
    )


async def add_user_to_dependencia(conn: asyncpg.Connection, user_id: uuid.UUID, depe_id: int) -> None:
    await conn.execute(
        "INSERT INTO urd_usuaroldep (user_id, depe_id, rol_id) VALUES ($1, $2, $3)",
        user_id,
        depe_id,
        await insert_group(conn),
    )


async def grant_clearance(conn: asyncpg.Connection, user_id: uuid.UUID, nivel: int) -> None:
    """Membresía en un grupo nuevo cuyo `role_clearance` es *nivel*."""
    group_id = await insert_group(conn)
    await conn.execute(
        "INSERT INTO auth_memberships (user_id, group_id) VALUES ($1, $2)", user_id, group_id
    )
    await conn.execute(
        "INSERT INTO role_clearance (group_id, max_level) VALUES ($1, $2)", group_id, nivel
    )


async def insert_dependencia(
    conn: asyncpg.Connection, codigo: int, nombre: str, activo: bool = True
) -> None:
    await conn.execute(
        "INSERT INTO dependencias (codigo, sigla, nombre, activo) VALUES ($1, $2, $3, $4)",
        codigo,
        f"D{codigo}",
        nombre,
        activo,
    )


@pytest.fixture(autouse=True)
def _sin_sincronizacion_de_estado() -> None:
    """Anula, para este directorio, el fixture autouse homónimo de
    `tests/conftest.py` (que vuelve no-op la sincronización del estado para el
    carril mockeado). Aquí la sincronización es EL SUT: corre de verdad."""
    return None
