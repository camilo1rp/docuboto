"""
init_tenant.py — Inicializar un nuevo tenant en OrpycaMCP.

Aplica migraciones globales (public schema) y por-tenant para todos los servicios,
luego registra el tenant en public.tenants.

Uso:
    python scripts/init_tenant.py --slug icetex --name "ICETEX" --code "ICETEX"

Variables de entorno requeridas:
    DATABASE_URL  postgresql+asyncpg://... o postgresql://...
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

# ---------------------------------------------------------------------------
# Rutas a las migraciones de cada servicio
# (relativas a la raíz del proyecto, montada en /project dentro del contenedor)
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent

# Each entry points to the service root; _sql_files() appends the sub-dir.
# Migrations live in migrations/global/*.sql  (schema public)
#                  and migrations/tenant/*.sql  (schema tenant_{slug}).
GLOBAL_MIGRATIONS = [
    BASE / "services" / "auth-service"   / "migrations" / "global",
    BASE / "services" / "tenant-service" / "migrations" / "global",
]

TENANT_MIGRATIONS = [
    BASE / "services" / "auth-service"    / "migrations" / "tenant",
    BASE / "services" / "document-service"  / "migrations" / "tenant",
    BASE / "services" / "storage-service"   / "migrations" / "tenant",
    BASE / "services" / "workflow-service"  / "migrations" / "tenant",
    BASE / "services" / "archive-service"   / "migrations" / "tenant",
    BASE / "services" / "signature-service" / "migrations" / "tenant",
    BASE / "services" / "notification-service" / "migrations" / "tenant",
]


def _dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def _sql_files(directory: Path) -> list[Path]:
    """Return sorted, numerically-prefixed .sql files in *directory*.

    Returns an empty list when the directory does not exist so that services
    without a given sub-dir (e.g. no global migrations) are silently skipped.
    """
    if not directory.is_dir():
        return []
    return sorted(
        f for f in directory.glob("*.sql")
        if re.match(r"^\d+_", f.name)
    )


# ---------------------------------------------------------------------------
# Migraciones globales — se rastrean en public._migrations
# ---------------------------------------------------------------------------

async def apply_global_migrations(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS public._migrations (
            id         SERIAL PRIMARY KEY,
            filename   TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    applied = {r["filename"] for r in await conn.fetch(
        "SELECT filename FROM public._migrations"
    )}
    for migrations_dir in GLOBAL_MIGRATIONS:
        for path in _sql_files(migrations_dir):
            if path.name in applied:
                print(f"  [global] skip  {path.name}")
                continue
            print(f"  [global] apply {path.name}")
            await conn.execute(path.read_text())
            await conn.execute(
                "INSERT INTO public._migrations (filename) VALUES ($1)", path.name
            )


# ---------------------------------------------------------------------------
# Schema del tenant — creado por tenant-service; aquí lo garantizamos
# ---------------------------------------------------------------------------

async def provision_schema(conn: asyncpg.Connection, slug: str) -> None:
    schema = f"tenant_{slug.replace('-', '_')}"
    print(f"  [schema] CREATE SCHEMA IF NOT EXISTS {schema}")
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


# ---------------------------------------------------------------------------
# Migraciones por tenant — se rastrean en tenant_{slug}._migrations
# para permitir el mismo filename en distintos tenants
# ---------------------------------------------------------------------------

async def apply_tenant_migrations(conn: asyncpg.Connection, slug: str) -> None:
    schema = f"tenant_{slug.replace('-', '_')}"
    await conn.execute(f"SET search_path = {schema}, public")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id         SERIAL PRIMARY KEY,
            filename   TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    applied = {r["filename"] for r in await conn.fetch(
        "SELECT filename FROM _migrations"
    )}
    for migrations_dir in TENANT_MIGRATIONS:
        for path in _sql_files(migrations_dir):
            if path.name in applied:
                print(f"  [{schema}] skip  {path.name}")
                continue
            print(f"  [{schema}] apply {path.name}")
            await conn.execute(path.read_text())
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)", path.name
            )
    # Restaurar search_path al terminar
    await conn.execute("SET search_path = public")


# ---------------------------------------------------------------------------
# Registro del tenant en public.tenants
# ---------------------------------------------------------------------------

async def register_tenant(
    conn: asyncpg.Connection, slug: str, name: str, code: str
) -> None:
    existing = await conn.fetchrow(
        "SELECT id, slug FROM public.tenants WHERE slug = $1", slug
    )
    if existing:
        print(f"  [tenant] ya existe — slug={slug}, id={existing['id']}")
        return
    row = await conn.fetchrow(
        """
        INSERT INTO public.tenants (slug, name, code, status)
        VALUES ($1, $2, $3, 'active')
        RETURNING id
        """,
        slug, name, code,
    )
    print(f"  [tenant] registrado — slug={slug}, id={row['id']}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main(slug: str, name: str, code: str) -> None:
    raw_dsn = os.environ.get("DATABASE_URL", "")
    if not raw_dsn:
        print("ERROR: DATABASE_URL no está definido", file=sys.stderr)
        sys.exit(1)

    dsn = _dsn(raw_dsn)
    print(f"\nConectando a PostgreSQL...")
    conn = await asyncpg.connect(dsn=dsn)

    try:
        print("\n── Migraciones globales (public schema) ──────────────────────")
        await apply_global_migrations(conn)

        print(f"\n── Schema del tenant: tenant_{slug.replace('-', '_')} ─────")
        await provision_schema(conn, slug)

        print(f"\n── Migraciones por tenant ────────────────────────────────────")
        await apply_tenant_migrations(conn, slug)

        print(f"\n── Registro en public.tenants ────────────────────────────────")
        await register_tenant(conn, slug, name, code)

        print(f"\n✓ Tenant '{slug}' inicializado correctamente.\n")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicializar tenant en OrpycaMCP")
    parser.add_argument("--slug", required=True, help="Slug único del tenant (ej: icetex)")
    parser.add_argument("--name", required=True, help="Nombre completo (ej: 'ICETEX')")
    parser.add_argument("--code", required=True, help="Código para radicados (ej: 'ICETEX')")
    args = parser.parse_args()

    # Validaciones básicas
    if not re.match(r"^[a-z0-9-]+$", args.slug):
        print("ERROR: slug solo puede contener minúsculas, números y guiones", file=sys.stderr)
        sys.exit(1)
    if not re.match(r"^[A-Z0-9_]+$", args.code):
        print("ERROR: code solo puede contener mayúsculas, números y guiones bajos", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(args.slug, args.name, args.code))
