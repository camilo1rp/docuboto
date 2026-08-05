# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Validación exigida por ADR-026 §"Modelo de datos" antes de integrar la
migración `039_tvd_instrumento_valoracion.sql`: aplicarla en DOS schemas de
tenant reales, en el MISMO proceso, en orden -- mismo modo de fallo histórico
que ya rompió las migraciones 016/017/019/035 (guard sin `current_schema()`
omite el objeto en el tenant #2 en silencio). Las VISTAS (`v_trd_series`/
`v_tvd_agrupaciones`) son el objeto NUEVO de esta migración y por tanto el
candidato a repetir el fallo.

Se ejecuta también, por MUTACIÓN, cada fila de la tabla de validación del ADR
(§"Modelo de datos", "Validación exigida antes de integrar"): un CHECK que
existe pero no se dispara es un CHECK que no está.

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

_MIGRATION_039_PATH = runner.TENANT_DIR / "039_tvd_instrumento_valoracion.sql"

_EXPECTED_TRD_SERIES_CONSTRAINTS = frozenset(
    {
        "trd_series_tipo_instrumento_check",
        "trd_series_tvd_forma_check",
        "trd_series_trd_forma_check",
    }
)
_EXPECTED_VIEWS = frozenset({"v_trd_series", "v_tvd_agrupaciones"})
_EXPECTED_TRIGGERS = frozenset({"trg_trd_series_parent_tipo"})
_EXPECTED_EXPEDIENTES_TRIGGERS = frozenset({"trg_expedientes_pin_tipo"})


@pytest_asyncio.fixture
async def two_bare_tenant_schemas(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[tuple[str, str, asyncpg.Connection], None]:
    """Provisiona DOS schemas `tenant_itest_<hex8>` con el historial COMPLETO
    de migraciones de tenant aplicado EN ORDEN (A primero, B después) --
    mismo criterio que `test_migration_037_dos_tenants.py`."""
    dsn = _require_dsn()
    conn = await asyncpg.connect(dsn=dsn)
    node_seed = f"{request.node.name}:{uuid.uuid4()}"
    slug_a = f"itest-{uuid.uuid4().hex[:8]}"
    slug_b = f"itest-{uuid.uuid4().hex[:8]}"
    schema_a = tenant_schema(slug_a)
    schema_b = tenant_schema(slug_b)
    assert node_seed

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


async def _view_names(conn: asyncpg.Connection, schema: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.views WHERE table_schema = $1",
        schema,
    )
    return {r["table_name"] for r in rows}


@pytest.mark.integration
async def test_migration_039_objects_exist_in_both_tenant_schemas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """El modo de fallo histórico: las VISTAS (objeto NUEVO de esta
    migración) deben existir en AMBOS schemas, no solo en el primero."""
    schema_a, schema_b, conn = two_bare_tenant_schemas

    for schema in (schema_a, schema_b):
        trd_constraints = await _constraint_names(conn, schema, "trd_series")
        missing = _EXPECTED_TRD_SERIES_CONSTRAINTS - trd_constraints
        assert not missing, f"{schema}: faltan constraints (trd_series) {missing}"

        views = await _view_names(conn, schema)
        missing_views = _EXPECTED_VIEWS - views
        assert not missing_views, f"{schema}: faltan vistas {missing_views}"

        trd_triggers = await _trigger_names(conn, schema, "trd_series")
        missing_triggers = _EXPECTED_TRIGGERS - trd_triggers
        assert not missing_triggers, f"{schema}: faltan triggers (trd_series) {missing_triggers}"

        exp_triggers = await _trigger_names(conn, schema, "expedientes")
        missing_exp_triggers = _EXPECTED_EXPEDIENTES_TRIGGERS - exp_triggers
        assert not missing_exp_triggers, (
            f"{schema}: faltan triggers (expedientes) {missing_exp_triggers}"
        )

        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'trd_series'",
            schema,
        )
        col_names = {r["column_name"] for r in cols}
        assert {
            "tipo_instrumento",
            "fondo_nombre",
            "fecha_extrema_inicial",
            "fecha_extrema_final",
            "justificacion_valoracion",
        } <= col_names


@pytest.mark.integration
async def test_migration_039_column_default_is_trd_in_both_schemas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        default = await conn.fetchval(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'trd_series' AND column_name = 'tipo_instrumento'",
            schema,
        )
        assert default is not None and "TRD" in default


@pytest.mark.integration
async def test_migration_039_is_idempotent_in_both_schemas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    schema_a, schema_b, conn = two_bare_tenant_schemas
    sql = _MIGRATION_039_PATH.read_text()

    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await conn.execute(sql)  # no debe lanzar

        trd_constraints = await _constraint_names(conn, schema, "trd_series")
        assert _EXPECTED_TRD_SERIES_CONSTRAINTS <= trd_constraints

        # El motor sigue operativo tras la re-ejecución: por mutación.
        code = f"IDEM{uuid.uuid4().hex[:6]}"
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            row = await conn.fetchrow(
                "INSERT INTO trd_series (code, name) VALUES ($1, 'itest post-idempotencia') "
                "RETURNING tipo_instrumento",
                code,
            )
        assert row["tipo_instrumento"] == "TRD"


# ---------------------------------------------------------------------------
# Tabla de validación del ADR-026 (§"Validación exigida antes de integrar"):
# una fila por mutación, ejercitada en AMBOS schemas.
# ---------------------------------------------------------------------------

_TVD_FONDO_COLS = "fondo_nombre, fecha_extrema_inicial, fecha_extrema_final, justificacion_valoracion"


@pytest.mark.integration
async def test_insert_tvd_sin_justificacion_valoracion_falla(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO trd_series (
                        code, name, tipo_instrumento, fondo_nombre,
                        fecha_extrema_inicial, fecha_extrema_final
                    ) VALUES ($1, 'itest sin justificacion', 'TVD', 'Fondo X',
                              '1980-01-01', '1990-01-01')
                    """,
                    f"CKTVD1{uuid.uuid4().hex[:6]}",
                )


@pytest.mark.integration
async def test_insert_tvd_con_archivo_gestion_years_falla(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await conn.execute(
                    f"""
                    INSERT INTO trd_series (
                        code, name, tipo_instrumento, archivo_gestion_years, {_TVD_FONDO_COLS}
                    ) VALUES ($1, 'itest con archivo gestion', 'TVD', 2,
                              'Fondo X', '1980-01-01', '1990-01-01', 'Justificacion')
                    """,
                    f"CKTVD2{uuid.uuid4().hex[:6]}",
                )


@pytest.mark.integration
async def test_insert_trd_con_fecha_extrema_final_falla(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO trd_series (code, name, tipo_instrumento, fecha_extrema_final)
                    VALUES ($1, 'itest TRD con fecha extrema', 'TRD', '1990-01-01')
                    """,
                    f"CKTRD1{uuid.uuid4().hex[:6]}",
                )


@pytest.mark.integration
async def test_insert_tvd_con_code_ya_usado_por_trd_vigente_falla(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Capa 1: el espacio de `code` es único y COMPARTIDO -- una TVD no
    puede reutilizar el código de una TRD (aquí, versión 1 de ambas, que
    colisiona en `trd_series_code_version_key` UNIQUE(code, version))."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        code = f"SHARED{uuid.uuid4().hex[:6]}"
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await conn.execute(
                "INSERT INTO trd_series (code, name) VALUES ($1, 'itest TRD ocupante')",
                code,
            )
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(asyncpg.exceptions.UniqueViolationError):
                await conn.execute(
                    f"""
                    INSERT INTO trd_series (code, name, tipo_instrumento, {_TVD_FONDO_COLS})
                    VALUES ($1, 'itest TVD colision', 'TVD',
                            'Fondo X', '1980-01-01', '1990-01-01', 'Justificacion')
                    """,
                    code,
                )


@pytest.mark.integration
async def test_update_tipo_instrumento_sobre_borrador_falla(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Regla (a) del trigger: `tipo_instrumento` es inmutable SIEMPRE,
    incluido `borrador` -- un cambio de tipo es una fila nueva, no una
    edición."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            serie = await conn.fetchrow(
                "INSERT INTO trd_series (code, name) VALUES ($1, 'itest borrador TRD') "
                "RETURNING id",
                f"MUT{uuid.uuid4().hex[:6]}",
            )
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(
                asyncpg.exceptions.PostgresError, match=r"tipo_instrumento.*es inmutable"
            ):
                await conn.execute(
                    "UPDATE trd_series SET tipo_instrumento = 'TVD' WHERE id = $1", serie["id"]
                )


@pytest.mark.integration
async def test_insert_via_v_tvd_agrupaciones_sin_tipo_instrumento_falla(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """`WITH CHECK OPTION`: un INSERT a través de la vista que se deja
    `tipo_instrumento` cae al DEFAULT 'TRD' -- la vista lo rechaza, en vez de
    crear una TRD disfrazada de TVD."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(asyncpg.exceptions.PostgresError):
                await conn.execute(
                    f"""
                    INSERT INTO v_tvd_agrupaciones (code, name, {_TVD_FONDO_COLS})
                    VALUES ($1, 'itest via vista sin tipo', 'Fondo X',
                            '1980-01-01', '1990-01-01', 'Justificacion')
                    """,
                    f"VWCK{uuid.uuid4().hex[:6]}",
                )


@pytest.mark.integration
async def test_v_trd_series_no_cuenta_tvd_sembradas(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Capa 2: `SELECT count(*) FROM v_trd_series` con TVD sembradas no las
    cuenta -- el aislamiento vive en el `FROM`, no en un `WHERE` opcional."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            before = await conn.fetchval("SELECT count(*) FROM v_trd_series")
            await conn.execute(
                f"""
                INSERT INTO trd_series (code, name, tipo_instrumento, {_TVD_FONDO_COLS})
                VALUES ($1, 'itest TVD sembrada', 'TVD',
                        'Fondo X', '1980-01-01', '1990-01-01', 'Justificacion')
                """,
                f"SEED{uuid.uuid4().hex[:6]}",
            )
            after_trd = await conn.fetchval("SELECT count(*) FROM v_trd_series")
            after_tvd = await conn.fetchval("SELECT count(*) FROM v_tvd_agrupaciones")
        assert after_trd == before
        assert after_tvd >= 1


@pytest.mark.integration
async def test_expediente_no_puede_amarrarse_a_tvd(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Capa 4 (D3): el trigger `trg_expedientes_pin_tipo` rechaza el amarre
    de un expediente `origen='corriente'` (DEFAULT de columna -- ninguno de
    los INSERT/UPDATE de este test declara `origen`) a una fila TVD. Se
    verifica tanto en INSERT como en UPDATE.

    Este test corre contra la cadena COMPLETA de migraciones (fixture
    `two_bare_tenant_schemas`), así que ejercita la regla VIGENTE -- desde la
    migración 041 (ADR-026, Increment 2), la Capa 4 quedó RELAJADA a
    `origen <-> tipo_instrumento` (ya no el bloqueo total de la 039, que
    ahora vive solo en el registro histórico de esta migración). El
    escenario que este test cubre (`origen='corriente'` intentando amarrarse
    a una TVD) sigue rechazado bajo la regla relajada -- solo cambió el
    mensaje del `RAISE EXCEPTION`, no el resultado. El amarre correcto de
    fondo acumulado (`origen='fondo_acumulado'` -> TVD) lo prueba
    `tests/integration/test_fondo_acumulado_amarre.py`."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            tvd = await conn.fetchrow(
                f"""
                INSERT INTO trd_series (code, name, tipo_instrumento, {_TVD_FONDO_COLS})
                VALUES ($1, 'itest TVD amarre', 'TVD',
                        'Fondo X', '1980-01-01', '1990-01-01', 'Justificacion')
                RETURNING id
                """,
                f"PIN{uuid.uuid4().hex[:6]}",
            )
            trd = await conn.fetchrow(
                "INSERT INTO trd_series (code, name) VALUES ($1, 'itest TRD amarre') "
                "RETURNING id",
                f"PINTRD{uuid.uuid4().hex[:6]}",
            )

        # INSERT directo a una TVD: rechazado.
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(
                asyncpg.exceptions.PostgresError, match=r"origen=corriente no puede amarrarse"
            ):
                await conn.execute(
                    "INSERT INTO expedientes (code, name, trd_serie_id) VALUES ($1, 'itest exp', $2)",
                    f"EXP{uuid.uuid4().hex[:6]}",
                    tvd["id"],
                )

        # UPDATE de un expediente EXISTENTE (amarrado a TRD) hacia una TVD: rechazado.
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            expediente = await conn.fetchrow(
                "INSERT INTO expedientes (code, name, trd_serie_id) VALUES ($1, 'itest exp trd', $2) "
                "RETURNING id",
                f"EXP2{uuid.uuid4().hex[:6]}",
                trd["id"],
            )
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(
                asyncpg.exceptions.PostgresError, match=r"origen=corriente no puede amarrarse"
            ):
                await conn.execute(
                    "UPDATE expedientes SET trd_serie_id = $2 WHERE id = $1",
                    expediente["id"],
                    tvd["id"],
                )

        # Contraparte: el mismo INSERT contra una TRD SÍ pasa (no es sobre-ancho).
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            ok = await conn.fetchrow(
                "INSERT INTO expedientes (code, name, trd_serie_id) VALUES ($1, 'itest exp ok', $2) "
                "RETURNING id",
                f"EXP3{uuid.uuid4().hex[:6]}",
                trd["id"],
            )
            assert ok["id"] is not None


@pytest.mark.integration
async def test_update_retention_years_sobre_borrador_no_se_rompio(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Última fila de la tabla de validación: la 039 NO rompió el
    comportamiento append-only ya validado por la 037/038 -- un `UPDATE`
    legítimo sobre `borrador` sigue pasando."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            serie = await conn.fetchrow(
                "INSERT INTO trd_series (code, name) VALUES ($1, 'itest borrador ok') "
                "RETURNING id",
                f"OKB{uuid.uuid4().hex[:6]}",
            )
            updated = await conn.fetchrow(
                "UPDATE trd_series SET retention_years = 99 WHERE id = $1 RETURNING retention_years",
                serie["id"],
            )
            assert updated["retention_years"] == 99


@pytest.mark.integration
async def test_trd_parent_no_puede_ser_tvd(
    two_bare_tenant_schemas: tuple[str, str, asyncpg.Connection],
) -> None:
    """Sección 4 de la migración: la jerarquía `parent_id` no cruza
    instrumentos."""
    schema_a, schema_b, conn = two_bare_tenant_schemas
    for schema in (schema_a, schema_b):
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            tvd = await conn.fetchrow(
                f"""
                INSERT INTO trd_series (code, name, tipo_instrumento, {_TVD_FONDO_COLS})
                VALUES ($1, 'itest TVD padre', 'TVD',
                        'Fondo X', '1980-01-01', '1990-01-01', 'Justificacion')
                RETURNING id
                """,
                f"PARENT{uuid.uuid4().hex[:6]}",
            )
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema}", public')
            with pytest.raises(
                asyncpg.exceptions.PostgresError, match=r"la jerarquia no cruza instrumentos"
            ):
                await conn.execute(
                    "INSERT INTO trd_series (code, name, parent_id) VALUES ($1, 'itest TRD hija', $2)",
                    f"CHILD{uuid.uuid4().hex[:6]}",
                    tvd["id"],
                )
