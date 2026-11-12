# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Libro único de migraciones (ADR-053), contra Postgres REAL.

`public.schema_migrations` identifica una migración por `(schema_name, filename)`:
un archivo se aplica UNA vez por esquema, y `apply_chain` salta lo ya aplicado.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest
from orpycamcp_common import migrations as ledger

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def schema(db_conn):
    """Un esquema vacío con el libro listo; se lleva sus filas del libro al irse."""
    name = f"tenant_ledger_{uuid.uuid4().hex[:8]}"
    await db_conn.execute(f'CREATE SCHEMA "{name}"')
    await ledger.ensure_ledger(db_conn)
    yield name
    await db_conn.execute(f'DROP SCHEMA "{name}" CASCADE')
    await db_conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", name)


def _chain(directory: Path, files: dict[str, str]) -> Path:
    for name, sql in files.items():
        (directory / name).write_text(sql, encoding="utf-8")
    return directory


async def test_la_clave_es_esquema_y_archivo(db_conn, schema):
    """El mismo archivo en dos esquemas son dos filas; dos veces en uno, ninguna."""
    await ledger.record(db_conn, schema, "010_a.sql")
    assert await ledger.applied(db_conn, schema) == {"010_a.sql"}
    assert "010_a.sql" not in await ledger.applied(db_conn, f"{schema}_otro")

    with pytest.raises(asyncpg.UniqueViolationError):
        await ledger.record(db_conn, schema, "010_a.sql")


async def test_apply_chain_aplica_en_orden_y_salta_lo_aplicado(db_conn, schema, tmp_path):
    chain = _chain(
        tmp_path,
        {
            "010_a.sql": "CREATE TABLE a (id int PRIMARY KEY);",
            "020_b.sql": "CREATE TABLE b (a_id int REFERENCES a(id));",
        },
    )
    await ledger.apply_chain(db_conn, chain, schema, log=lambda _line: None)
    assert await ledger.applied(db_conn, schema) == {"010_a.sql", "020_b.sql"}

    # Segunda pasada: sin `IF NOT EXISTS`, reaplicar `CREATE TABLE a` fallaría.
    _chain(tmp_path, {"030_c.sql": "CREATE TABLE c (id int);"})
    aplicados: list[str] = []
    await ledger.apply_chain(db_conn, chain, schema, log=aplicados.append)
    assert len(aplicados) == 1 and "030_c.sql" in aplicados[0]
    assert await db_conn.fetchval("SELECT to_regclass($1)", f"{schema}.c") is not None


async def test_un_archivo_que_falla_no_queda_anotado(db_conn, schema, tmp_path):
    """Cada archivo va en su transacción junto con su fila del libro."""
    chain = _chain(
        tmp_path,
        {
            "010_a.sql": "CREATE TABLE a (id int);",
            "020_roto.sql": "CREATE TABLE b (id int); SELECT 1 / 0;",
        },
    )
    with pytest.raises(asyncpg.DivisionByZeroError):
        await ledger.apply_chain(db_conn, chain, schema, log=lambda _line: None)
    assert await ledger.applied(db_conn, schema) == {"010_a.sql"}
    assert await db_conn.fetchval("SELECT to_regclass($1)", f"{schema}.b") is None
