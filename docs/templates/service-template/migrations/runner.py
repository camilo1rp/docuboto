"""
Aplica migraciones SQL pendientes (ADR-012).

Dos clases por convención de carpetas:
    migrations/global/   → schema public (track: public._migrations)
    migrations/tenant/   → cada schema tenant_{slug} (track: public._tenant_migrations)

Uso:
    python migrations/runner.py

Lee DATABASE_URL del entorno. Idempotente y re-ejecutable.
"""

import asyncio
import os
import re
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent
GLOBAL_DIR = MIGRATIONS_DIR / "global"
TENANT_DIR = MIGRATIONS_DIR / "tenant"
DSN = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _sql_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(f for f in directory.glob("*.sql") if re.match(r"^\d+_", f.name))


async def _ensure_tracking(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public._migrations (
            id          SERIAL PRIMARY KEY,
            filename    TEXT NOT NULL UNIQUE,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public._tenant_migrations (
            id           SERIAL PRIMARY KEY,
            schema_name  TEXT NOT NULL,
            filename     TEXT NOT NULL,
            applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (schema_name, filename)
        )
        """
    )


async def _apply_global(conn: asyncpg.Connection) -> None:
    files = _sql_files(GLOBAL_DIR)
    if not files:
        return
    applied = {
        row["filename"]
        for row in await conn.fetch("SELECT filename FROM public._migrations")
    }
    await conn.execute("SET search_path = public")
    for path in files:
        if path.name in applied:
            print(f"  skip  [global] {path.name}")
            continue
        print(f"  apply [global] {path.name}")
        await conn.execute(path.read_text())
        await conn.execute(
            "INSERT INTO public._migrations (filename) VALUES ($1)", path.name
        )


async def _tenant_schemas(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        r"""
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name LIKE 'tenant\_%' ESCAPE '\'
        ORDER BY schema_name
        """
    )
    return [r["schema_name"] for r in rows]


async def _apply_tenant(conn: asyncpg.Connection) -> None:
    files = _sql_files(TENANT_DIR)
    if not files:
        return
    schemas = await _tenant_schemas(conn)
    if not schemas:
        print("  (sin schemas tenant_*; migraciones de tenant diferidas)")
        return
    for schema in schemas:
        applied = {
            row["filename"]
            for row in await conn.fetch(
                "SELECT filename FROM public._tenant_migrations WHERE schema_name = $1",
                schema,
            )
        }
        for path in files:
            if path.name in applied:
                print(f"  skip  [{schema}] {path.name}")
                continue
            print(f"  apply [{schema}] {path.name}")
            # schema viene de information_schema (creado por tenant-service con slug
            # validado); seguro para interpolar como identificador.
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO public._tenant_migrations (schema_name, filename) "
                    "VALUES ($1, $2)",
                    schema,
                    path.name,
                )
        await conn.execute("SET search_path = public")


def _warn_stray_root_files() -> None:
    stray = _sql_files(MIGRATIONS_DIR)
    if stray:
        names = ", ".join(p.name for p in stray)
        print(
            f"  WARN  ignorando .sql en la raíz de migrations/ ({names}); "
            "muévelos a global/ o tenant/ (ADR-012)"
        )


async def run() -> None:
    conn = await asyncpg.connect(dsn=DSN)
    try:
        await _ensure_tracking(conn)
        _warn_stray_root_files()
        await _apply_global(conn)
        await _apply_tenant(conn)
        print("migrations done")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
