# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One runner: global folder, then tenant folder. The SQL is the generated baseline.

A database that already ran the nine domain runners is stamped, not replayed.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import asyncpg
from orpycamcp_common import migrations as ledger

SERVICIO = "docuboto"

MIGRATIONS_DIR = Path(__file__).parent
GLOBAL_DIR = MIGRATIONS_DIR / "global"
TENANT_DIR = MIGRATIONS_DIR / "tenant"
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""


def _require_dsn() -> str:
    if not DSN:
        raise RuntimeError(
            "DATABASE_URL no está definida: el runner de migraciones necesita la "
            "cadena de conexión a PostgreSQL."
        )
    return DSN


def _sql_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(f for f in directory.glob("*.sql") if re.match(r"^\d+_", f.name))


def _mapa_propio(directory: Path) -> dict[str, list[str]]:
    return {p.name: [SERVICIO] for p in _sql_files(directory)}


def _ya_provisión_antigua(aplicadas: set[tuple[str, str]]) -> bool:
    return any(svc != SERVICIO for svc, _nombre in aplicadas)


async def _sello(conn: asyncpg.Connection, esquema: str, files: list[Path]) -> None:
    for path in files:
        await ledger.record(conn, esquema, SERVICIO, path.name)


async def _apply_global(conn: asyncpg.Connection) -> None:
    files = _sql_files(GLOBAL_DIR)
    if not files:
        return
    aplicadas = await ledger.applied(conn, ledger.ESQUEMA_GLOBAL)
    await conn.execute("SET search_path = public")
    if _ya_provisión_antigua(aplicadas):
        await _sello(conn, ledger.ESQUEMA_GLOBAL, files)
        print("  stamp [global] baseline (nine runners already applied)")
        return
    for path in files:
        if (SERVICIO, path.name) in aplicadas:
            print(f"  skip  [global] {path.name}")
            continue
        print(f"  apply [global] {path.name}")
        async with conn.transaction():
            await conn.execute(path.read_text())
            await ledger.record(conn, ledger.ESQUEMA_GLOBAL, SERVICIO, path.name)


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
        aplicadas = await ledger.applied(conn, schema)
        if _ya_provisión_antigua(aplicadas):
            await _sello(conn, schema, files)
            print(f"  stamp [{schema}] baseline")
            continue
        for path in files:
            if (SERVICIO, path.name) in aplicadas:
                print(f"  skip  [{schema}] {path.name}")
                continue
            print(f"  apply [{schema}] {path.name}")
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}"')
                await conn.execute(path.read_text())
                await ledger.record(conn, schema, SERVICIO, path.name)
        await conn.execute("SET search_path = public")


def _warn_stray_root_files() -> None:
    stray = _sql_files(MIGRATIONS_DIR)
    if stray:
        names = ", ".join(p.name for p in stray)
        print(f"  WARN  ignorando .sql en la raíz de migrations/ ({names})")


async def run() -> None:
    conn = await asyncpg.connect(dsn=_require_dsn())
    try:
        async with ledger.cerrojo(conn):
            await ledger.ensure_ledger(conn)
            for aviso in await ledger.importar_libros_antiguos(
                conn,
                servicios_por_archivo_global=_mapa_propio(GLOBAL_DIR),
                servicios_por_archivo_tenant=_mapa_propio(TENANT_DIR),
                ignorar_desconocidos=True,
            ):
                print(aviso)
            _warn_stray_root_files()
            await _apply_global(conn)
            await _apply_tenant(conn)
            print("migrations done")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
