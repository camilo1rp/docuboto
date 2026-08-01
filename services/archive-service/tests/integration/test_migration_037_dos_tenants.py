# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Validación exigida por ADR-025 §"Modelo de datos" antes de integrar la
migración `037_trd_versionado_convalidacion.sql`: aplicarla en **DOS**
schemas de tenant reales, en el MISMO proceso, en orden -- es justo donde se
esconde el modo de fallo histórico de este proyecto (migraciones 016/017/019/
035, reparadas por la 036): un guard de idempotencia que filtra por nombre de
objeto SIN `current_schema()` encuentra el objeto ya creado en el schema #1 y
**omite en silencio** crearlo en el schema #2 y siguientes -- sin error, sin
skip visible, la provisión del segundo tenant queda a medias.

Deliberadamente NO se usa `tenant_ctx` (que ya aplica el historial completo a
UN schema por test): aquí se controlan DOS schemas a mano, en la MISMA
sesión de test, para poder comparar objeto-por-objeto entre ambos -- es la
comparación que un test de un-solo-schema no puede hacer por construcción.

Gating: `RUN_INTEGRATION=1` + Postgres real (ver CLAUDE.md y
`tests/integration/conftest.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import tenant_schema
from migrations import runner

from .conftest import _require_dsn

_MIGRATION_037_PATH = runner.TENANT_DIR / "037_trd_versionado_convalidacion.sql"

# Objetos que 037 introduce y que deben existir en CADA schema, no solo en el
# primero que se migró.
_EXPECTED_TRD_SERIES_CONSTRAINTS = frozenset(
    {
        "trd_series_estado_check",
        "trd_series_convalidada_acto_check",
        "trd_series_migrada_sin_acto_check",
        "trd_series_vigencia_check",
        "trd_series_code_version_key",
    }
)
_EXPECTED_EXPEDIENTES_CONSTRAINTS = frozenset({"expedientes_trd_serie_id_fkey"})
# Retro-compatibilidad con el resto de este módulo (idempotencia/CHECK por
# mutación, que solo ejercitan `trd_series`).
_EXPECTED_CONSTRAINTS = _EXPECTED_TRD_SERIES_CONSTRAINTS
_EXPECTED_INDEXES = frozenset({"ux_trd_series_vigente", "ux_trd_series_en_tramite"})
_EXPECTED_TRIGGERS = frozenset(
    {"trg_trd_series_append_only", "trg_trd_series_no_truncate", "trg_expedientes_trd_pin_immutable"}
)


@pytest_asyncio.fixture
async def two_bare_tenant_schemas(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[tuple[str, str, asyncpg.Connection], None]:
    """Provisiona DOS schemas `tenant_itest_<hex8>` con el historial COMPLETO
    de migraciones de tenant aplicado EN ORDEN (A primero, B después) --
    mismo criterio de idempotencia/`current_schema()` que la provisión real
    (`init_tenant.py`), pero sin pasar por el tracking de `public.
    _tenant_migrations` (no aporta nada a este test y acopla con otros
    módulos que también usan `db_conn`/`public`)."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    node_seed = f"{request.node.name}:{uuid.uuid4()}"
    slug_a = f"itest-{uuid.uuid4().hex[:8]}"
    slug_b = f"itest-{uuid.uuid4().hex[:8]}"
    schema_a = tenant_schema(slug_a)
    schema_b = tenant_schema(slug_b)
    assert node_seed  # solo para trazabilidad, no participa en el hash

    files = runner._sql_files(runner.TENANT_DIR)
    try:
        for schema in (schema_a, schema_b):
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            for path in files:
                async with conn.transaction():
                    await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                    await conn.execute(path.read_text())
        yield schema_a, schema_b, conn
    finally:
        for schema in (schema_a, schema_b):
            assert schema.startswith("tenant_itest_")
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


async def _constraint_names(conn: asyncpg.Connection, schema: str, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT c.conname FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = $1 AND t.relname = $2
        """,
        schema,
        table,
    )
    return {r["conname"] for r in rows}


async def _index_names(conn: asyncpg.Connection, schema: str, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT i.relname FROM pg_index x
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_class t ON t.oid = x.indrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = $1 AND t.relname = $2
        """,
        schema,
        table,
    )
    return {r["relname"] for r in rows}


async def _trigger_names(conn: asyncpg.Connection, schema: str, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT tg.tgname FROM pg_trigger tg
          JOIN pg_class t ON t.oid = tg.tgrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = $1 AND t.relname = $2 AND NOT tg.tgisinternal
        """,
        schema,
        table,
    )
    return {r["tgname"] for r in rows}


@pytest.mark.integration
async def test_migration_037_objects_exist_in_both_tenant_schemas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """El modo de fallo histórico: un guard sin `current_schema()` deja el
    tenant #2 (y siguientes) SIN los objetos de 037, en silencio. Se
    comprueba objeto-por-objeto en AMBOS schemas -- constraints, índices y
    triggers."""
    schema_a, schema_b, conn = two_bare_tenant_schemas

    for schema in (schema_a, schema_b):
        trd_constraints = await _constraint_names(conn, schema, "trd_series")
        missing_constraints = _EXPECTED_TRD_SERIES_CONSTRAINTS - trd_constraints
        assert not missing_constraints, f"{schema}: faltan constraints (trd_series) {missing_constraints}"

        exp_constraints = await _constraint_names(conn, schema, "expedientes")
        missing_exp_constraints = _EXPECTED_EXPEDIENTES_CONSTRAINTS - exp_constraints
        assert not missing_exp_constraints, (
            f"{schema}: faltan constraints (expedientes) {missing_exp_constraints}"
        )

        trd_indexes = await _index_names(conn, schema, "trd_series")
        missing_indexes = _EXPECTED_INDEXES - trd_indexes
        assert not missing_indexes, f"{schema}: faltan índices {missing_indexes}"

        trd_triggers = await _trigger_names(conn, schema, "trd_series")
        exp_triggers = await _trigger_names(conn, schema, "expedientes")
        all_triggers = trd_triggers | exp_triggers
        missing_triggers = _EXPECTED_TRIGGERS - all_triggers
        assert not missing_triggers, f"{schema}: faltan triggers {missing_triggers}"

        # Columnas nuevas de `trd_series`/`expedientes` -- confirmadas en
        # AMBOS schemas, no solo en el primero.
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'trd_series'",
            schema,
        )
        col_names = {r["column_name"] for r in cols}
        assert {"estado", "valid_to", "acto_administrativo", "supersede_a", "created_by"} <= col_names

        exp_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'expedientes'",
            schema,
        )
        exp_col_names = {r["column_name"] for r in exp_cols}
        assert {"trd_serie_code", "trd_revision_requerida", "trd_revision_motivo"} <= exp_col_names


@pytest.mark.integration
async def test_migration_037_column_default_is_borrador_in_both_schemas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """El DEFAULT de columna para filas NUEVAS es `borrador` (no `migrada`,
    que es solo el valor del BACKFILL) -- confirmado en ambos schemas."""
    schema_a, schema_b, conn = two_bare_tenant_schemas

    for schema in (schema_a, schema_b):
        default = await conn.fetchval(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'trd_series' AND column_name = 'estado'",
            schema,
        )
        assert default is not None and "borrador" in default


@pytest.mark.integration
async def test_migration_037_backfill_leaves_preexisting_rows_migrada_without_acto_data(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """D6: una fila insertada ANTES de 037 (simulada aquí insertando en el
    schema recién migrado, sin especificar `estado` -- caería en el DEFAULT
    de columna que ADR-025 exige que sea `'migrada'` para filas
    preexistentes) -- se valida el backfill real reconstruyendo el escenario:
    insertar la fila en el punto exacto en que 037 corre (columna aún sin
    default de 'borrador'), y confirmando que 037 la deja `migrada` sin
    datos de acto. Se ejercita en AMBOS schemas."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    files = [p for p in runner._sql_files(runner.TENANT_DIR) if p.name != "037_trd_versionado_convalidacion.sql"]

    schema_a = tenant_schema(f"itest-{uuid.uuid4().hex[:8]}")
    schema_b = tenant_schema(f"itest-{uuid.uuid4().hex[:8]}")
    try:
        for schema in (schema_a, schema_b):
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            for path in files:
                async with conn.transaction():
                    await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                    await conn.execute(path.read_text())

            # Fila PRE-037: sin `estado` (la columna ni siquiera existe todavía).
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await conn.execute(
                    "INSERT INTO trd_series (code, name) VALUES ($1, 'itest pre-037')",
                    "PRE037",
                )

            # Ahora corre 037 (el guard `current_schema()` debe verla, per-schema).
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await conn.execute(_MIGRATION_037_PATH.read_text())

            row = await conn.fetchrow(
                f'SELECT estado, acto_administrativo, fecha_convalidacion, '
                f'instancia_convalidante, rusd_radicado, version FROM "{schema}".trd_series '
                "WHERE code = $1",
                "PRE037",
            )
            assert row["estado"] == "migrada", f"{schema}: backfill no dejó 'migrada'"
            assert row["version"] == 1
            assert row["acto_administrativo"] is None
            assert row["fecha_convalidacion"] is None
            assert row["instancia_convalidante"] is None
            assert row["rusd_radicado"] is None
    finally:
        for schema in (schema_a, schema_b):
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


@pytest.mark.integration
async def test_migration_037_is_idempotent_in_both_schemas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Re-ejecutar 037 sobre un schema YA migrado (por partida doble, en
    ambos) no falla ni duplica constraints/índices/triggers."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    sql = _MIGRATION_037_PATH.read_text()

    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await conn.execute(sql)  # no debe lanzar

        trd_constraints = await _constraint_names(conn, schema, "trd_series")
        # Ningún duplicado -- `pg_constraint` no puede tener dos filas con el
        # mismo `conname` sobre la misma tabla, así que basta con que el
        # `IF NOT EXISTS` no haya reventado (ya verificado arriba) y que los
        # constraints esperados sigan siendo exactamente los mismos.
        assert _EXPECTED_CONSTRAINTS <= trd_constraints

        # El motor sigue operativo tras la re-ejecución: por mutación.
        code = f"IDEM{uuid.uuid4().hex[:6]}"
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            row = await conn.fetchrow(
                "INSERT INTO trd_series (code, name) VALUES ($1, 'itest post-idempotencia') "
                "RETURNING estado",
                code,
            )
        assert row["estado"] == "borrador"  # DEFAULT de columna para fila NUEVA


@pytest.mark.integration
async def test_check_constraint_active_by_mutation_in_both_schemas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Por MUTACIÓN, en ambos schemas: un `INSERT` que viola
    `trd_series_migrada_sin_acto_check` debe fallar -- un CHECK que existe
    pero no se dispara es un CHECK que no está (instrucción explícita de la
    validación exigida por el ADR)."""
    schema_a, schema_b, conn = two_bare_tenant_schemas

    for schema in (schema_a, schema_b):
        code = f"CKM{uuid.uuid4().hex[:6]}"
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO trd_series (code, name, estado, acto_administrativo)
                    VALUES ($1, 'itest CHECK activo', 'migrada', 'no deberia poder')
                    """,
                    code,
                )

        # Y sobre `borrador` (una fila nueva, columna en su DEFAULT real), el
        # mismo UPDATE de contenido SÍ pasa -- confirma que el trigger no es
        # sobre-ancho (falso positivo bloqueando lo legítimo).
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            serie = await conn.fetchrow(
                "INSERT INTO trd_series (code, name) VALUES ($1, 'itest borrador editable') "
                "RETURNING id, estado",
                f"OKB{uuid.uuid4().hex[:6]}",
            )
            assert serie["estado"] == "borrador"
            updated = await conn.fetchrow(
                "UPDATE trd_series SET retention_years = 99 WHERE id = $1 RETURNING retention_years",
                serie["id"],
            )
            assert updated["retention_years"] == 99

        # ... pero la MISMA mutación sobre una fila `migrada` (backfill,
        # DEFAULT NO aplica -- se fuerza el estado explícito) SÍ la rechaza
        # el trigger append-only (`trg_trd_series_append_only`).
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            migrada = await conn.fetchrow(
                "INSERT INTO trd_series (code, name, estado) VALUES ($1, 'itest migrada', 'migrada') "
                "RETURNING id",
                f"MGB{uuid.uuid4().hex[:6]}",
            )
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(
                asyncpg.exceptions.PostgresError, match=r"contenido inmutable en estado"
            ):
                await conn.execute(
                    "UPDATE trd_series SET retention_years = 99 WHERE id = $1", migrada["id"]
                )
