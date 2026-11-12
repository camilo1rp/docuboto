# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Disposable tenant schemas built from the single migration chain (ADR-053).

`public` is migrated beforehand by `services/docuboto/migrations/runner.py`;
nothing here applies the global chain.
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg
from orpycamcp_common import migrations

#: The single migration chain of the repo.
CHAIN = Path(__file__).resolve().parents[4] / "services" / "docuboto" / "migrations"


async def provision_schema(conn: asyncpg.Connection, schema: str) -> None:
    """Create *schema* with the whole tenant chain and leave it as the search path."""
    assert schema.startswith("tenant_itest_")
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await migrations.apply_chain(conn, CHAIN / "tenant", schema, log=lambda _line: None)
    await conn.execute(f'SET search_path = "{schema}"')


async def drop_schema(conn: asyncpg.Connection, schema: str) -> None:
    assert schema.startswith("tenant_itest_")
    await conn.execute("RESET search_path")
    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)


_FILLERS = {
    "uuid": "gen_random_uuid()",
    "int2": "1",
    "int4": "1",
    "int8": "1",
    "numeric": "1",
    "varchar": "'x'",
    "bpchar": "'x'",
    "text": "'x'",
    "bool": "false",
    "date": "current_date",
    "timestamptz": "now()",
    "jsonb": "'{}'::jsonb",
}


async def seed_row(
    conn: asyncpg.Connection, table: str, *, schema: str | None = None, **values: object
) -> None:
    """Insert one row into a REAL table of another module, giving only the columns the test is about.

    The remaining mandatory columns get a filler of their type. Foreign keys and
    row triggers are off for this one insert (`session_replication_role =
    replica`, owner connection): the tests that use it check how tenant-service
    reads a column of that table, not the rules of the module that owns it.
    NOT NULL and CHECK constraints still apply.
    """
    schema = schema or await conn.fetchval("SELECT current_schema()")
    pending = await conn.fetch(
        "SELECT a.attname, t.typname FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid "
        "WHERE a.attrelid = format('%I.%I', $1::text, $2::text)::regclass AND a.attnum > 0 "
        "  AND NOT a.attisdropped AND a.attnotnull AND NOT a.atthasdef "
        "  AND a.attidentity = '' AND a.attgenerated = ''",
        schema,
        table,
    )
    columns = list(values)
    exprs = [f"${i}" for i in range(1, len(columns) + 1)]
    for row in pending:
        if row["attname"] not in values:
            columns.append(row["attname"])
            exprs.append(_FILLERS[row["typname"]])
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            f'INSERT INTO "{schema}"."{table}" ({", ".join(columns)}) VALUES ({", ".join(exprs)})',
            *(json.dumps(v) if isinstance(v, (dict, list)) else v for v in values.values()),
        )
