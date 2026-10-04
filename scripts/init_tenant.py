# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
init_tenant.py — Inicializar un nuevo tenant en OrpycaMCP.

Aplica migraciones globales (public schema) y por-tenant para todos los servicios,
luego registra el tenant en public.tenants.

Lo aplicado se anota en el libro único `public.schema_migrations`, compartido con
`services/*/migrations/runner.py` (ver `orpycamcp_common.migrations`). Antes cada
ejecutor llevaba su propia contabilidad con claves distintas y no se veían entre
sí: uno reaplicaba lo que el otro ya había hecho, y el nombre desnudo del archivo
—que se repite entre servicios— hacía saltar migraciones en silencio.

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
from orpycamcp_common import migrations as ledger

# ---------------------------------------------------------------------------
# Rutas a las migraciones de cada servicio
# (relativas a la raíz del proyecto, montada en /project dentro del contenedor)
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent

# Each entry points to the service root; _sql_files() appends the sub-dir.
# Migrations live in migrations/global/*.sql  (schema public)
#                  and migrations/tenant/*.sql  (schema tenant_{slug}).
# Las GLOBALES se aplican SIEMPRE antes que las de tenant (ver main()), lo que
# importa porque instalan extensiones de las que dependen tablas por-tenant:
# `document-service/001_extensions.sql` (pg_trgm/unaccent → FTS) y
# `knowledge-service/001_pgvector.sql` (pgvector → columnas `vector(384)` de
# `knowledge_chunk`). Omitirlas hacía fallar la provisión de esos servicios.
GLOBAL_MIGRATIONS = [
    BASE / "services" / "auth-service"   / "migrations" / "global",
    BASE / "services" / "tenant-service" / "migrations" / "global",
    BASE / "services" / "document-service"  / "migrations" / "global",
    BASE / "services" / "knowledge-service" / "migrations" / "global",
]

# ORDEN: `auth-service` primero (RBAC/usuarios) y `tenant-service` a continuación
# por ser fundacional (dependencias, parámetros, catálogos, PINAR); los servicios
# de dominio después; `knowledge-service` al final, por ser una capa DERIVADA
# (índice advisorio, no fuente de verdad — ADR-006).
#
# Esta lista debe cubrir TODO servicio con `migrations/tenant/`. Al añadir un
# servicio nuevo, añadirlo también aquí: una omisión no da error: el tenant se
# provisiona "bien" y el servicio falla en runtime contra tablas inexistentes.
TENANT_MIGRATIONS = [
    BASE / "services" / "auth-service"    / "migrations" / "tenant",
    BASE / "services" / "tenant-service"    / "migrations" / "tenant",
    BASE / "services" / "document-service"  / "migrations" / "tenant",
    BASE / "services" / "storage-service"   / "migrations" / "tenant",
    BASE / "services" / "workflow-service"  / "migrations" / "tenant",
    BASE / "services" / "archive-service"   / "migrations" / "tenant",
    BASE / "services" / "signature-service" / "migrations" / "tenant",
    BASE / "services" / "notification-service" / "migrations" / "tenant",
    BASE / "services" / "knowledge-service" / "migrations" / "tenant",
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


def _servicio_de(migrations_dir: Path) -> str:
    """…/services/<svc>/migrations/<ámbito> → `<svc>`."""
    return migrations_dir.parent.parent.name


def _mapa_servicios(dirs: list[Path]) -> dict[str, list[str]]:
    """`{archivo: [servicios que lo declaran]}` para el import del libro único.

    Es lo que permite devolverle el servicio a las entradas antiguas, que
    guardaban el nombre desnudo. Cuatro nombres los declaran DOS servicios
    (`002_batch_jobs.sql` en archive/document; `005`, `006` y `007` de
    dead-letter en notification/signature): la ambigüedad no se puede resolver
    leyendo el registro, y `orpycamcp_common.migrations` la resuelve avisando en
    vez de en silencio.
    """
    mapa: dict[str, list[str]] = {}
    for d in dirs:
        for path in _sql_files(d):
            mapa.setdefault(path.name, []).append(_servicio_de(d))
    return mapa


# ---------------------------------------------------------------------------
# Migraciones globales — se anotan en el libro único public.schema_migrations
# ---------------------------------------------------------------------------

async def apply_global_migrations(conn: asyncpg.Connection) -> None:
    aplicadas = await ledger.applied(conn, ledger.ESQUEMA_GLOBAL)
    for migrations_dir in GLOBAL_MIGRATIONS:
        servicio = _servicio_de(migrations_dir)
        for path in _sql_files(migrations_dir):
            if (servicio, path.name) in aplicadas:
                print(f"  [global] skip  {servicio}/{path.name}")
                continue
            print(f"  [global] apply {servicio}/{path.name}")
            await conn.execute(path.read_text())
            await ledger.record(conn, ledger.ESQUEMA_GLOBAL, servicio, path.name)
            # Alimentar el conjunto en caliente: se leyó UNA vez antes del bucle.
            aplicadas.add((servicio, path.name))


# ---------------------------------------------------------------------------
# Schema del tenant — creado por tenant-service; aquí lo garantizamos
# ---------------------------------------------------------------------------

async def provision_schema(conn: asyncpg.Connection, slug: str) -> None:
    schema = f"tenant_{slug.replace('-', '_')}"
    print(f"  [schema] CREATE SCHEMA IF NOT EXISTS {schema}")
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    # El rol de la aplicación necesita USAR el esquema nuevo: los privilegios por
    # omisión cubren sus tablas, no el esquema en sí (ver migración 008).
    if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app'"):
        await conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO orpycamcp_app")
        print(f"  [schema] GRANT USAGE ON {schema} TO orpycamcp_app")


# ---------------------------------------------------------------------------
# Migraciones por tenant — mismo libro único, con `schema_name = tenant_{slug}`.
#
# Antes vivían en `tenant_{slug}._migrations`, invisible para el registro que usa
# `services/*/migrations/runner.py` (`public._tenant_migrations`): provisionar
# con un ejecutor y actualizar con el otro reaplicaba TODAS las migraciones.
# ---------------------------------------------------------------------------

async def apply_tenant_migrations(conn: asyncpg.Connection, slug: str) -> None:
    schema = f"tenant_{slug.replace('-', '_')}"
    await conn.execute(f"SET search_path = {schema}, public")
    aplicadas = await ledger.applied(conn, schema)
    for migrations_dir in TENANT_MIGRATIONS:
        servicio = _servicio_de(migrations_dir)
        for path in _sql_files(migrations_dir):
            if (servicio, path.name) in aplicadas:
                print(f"  [{schema}] skip  {servicio}/{path.name}")
                continue
            print(f"  [{schema}] apply {servicio}/{path.name}")
            await conn.execute(path.read_text())
            await ledger.record(conn, schema, servicio, path.name)
            aplicadas.add((servicio, path.name))
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
        # Una entidad dada de alta por `POST /api/v1/tenants` nace `pending`
        # (solo registro, sin esquema): esta provisión es la que la completa.
        activated = await conn.execute(
            "UPDATE public.tenants SET provisioning_status = 'active', updated_at = now() "
            "WHERE slug = $1 AND provisioning_status <> 'active'",
            slug,
        )
        if activated != "UPDATE 0":
            print(f"  [tenant] provisioning_status pending -> active — slug={slug}")
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
    raw_dsn = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not raw_dsn:
        print("ERROR: DATABASE_URL no está definido", file=sys.stderr)
        sys.exit(1)

    dsn = _dsn(raw_dsn)
    print("\nConectando a PostgreSQL...")
    conn = await asyncpg.connect(dsn=dsn)

    try:
        print("\n── Libro de migraciones ──────────────────────────────────────")
        await ledger.ensure_ledger(conn)
        # Import único de los tres registros antiguos. Este ejecutor ve los
        # directorios de todos los servicios, así que puede atribuir cada nombre
        # desnudo; un nombre que no reconoce es una migración retirada del
        # código y merece aviso (de ahí `ignorar_desconocidos` por defecto).
        for aviso in await ledger.importar_libros_antiguos(
            conn,
            servicios_por_archivo_global=_mapa_servicios(GLOBAL_MIGRATIONS),
            servicios_por_archivo_tenant=_mapa_servicios(TENANT_MIGRATIONS),
        ):
            print(aviso)

        print("\n── Migraciones globales (public schema) ──────────────────────")
        await apply_global_migrations(conn)

        print(f"\n── Schema del tenant: tenant_{slug.replace('-', '_')} ─────")
        await provision_schema(conn, slug)

        print("\n── Migraciones por tenant ────────────────────────────────────")
        await apply_tenant_migrations(conn, slug)

        print("\n── Registro en public.tenants ────────────────────────────────")
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
