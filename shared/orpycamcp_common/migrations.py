# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""The migration chain, its ledger and its lock (ADR-053).

One chain lives in `services/docuboto/migrations`: `global/` goes to `public`,
`tenant/` goes to every `tenant_*` schema. Each file runs in its own
transaction and is written down in `public.schema_migrations` under
`(schema_name, filename)`. The runner and `scripts/init_tenant.py` both call
this module, so they cannot drift apart.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

LEDGER = "public.schema_migrations"

# ---------------------------------------------------------------------------
# Cerrojo global de migraciones (C19, ADR-041)
# ---------------------------------------------------------------------------
#
# El runner y el alta de una entidad (`scripts/init_tenant.py`) comparten el
# libro y `public`: dos a la vez se pisarían en `CREATE TABLE IF NOT EXISTS` y en
# comprobar-y-luego-escribir. Constante fija y legible (los
# ocho bytes ASCII de «ORPYCAM1») en vez de `hashtext(...)`, para reconocerla
# en `pg_locks` sin tener que recalcular nada. Nivel de SESIÓN: sobrevive a las
# transacciones por archivo y se libera sola si la conexión muere.
CERROJO_MIGRACIONES = 0x4F52505943414D31

PLAZO_CERROJO_POR_DEFECTO_S = 600.0
_ESPERA_ENTRE_INTENTOS_S = 0.5


def plazo_cerrojo_s() -> float:
    """`MIGRATIONS_LOCK_TIMEOUT_S` (segundos); 600 si no está. Valor inválido ⇒ error."""
    crudo = os.environ.get("MIGRATIONS_LOCK_TIMEOUT_S", "").strip()
    if not crudo:
        return PLAZO_CERROJO_POR_DEFECTO_S
    try:
        valor = float(crudo)
    except ValueError:
        raise RuntimeError(
            f"MIGRATIONS_LOCK_TIMEOUT_S={crudo!r} no es un número de segundos"
        ) from None
    if valor <= 0:
        raise RuntimeError("MIGRATIONS_LOCK_TIMEOUT_S debe ser mayor que 0")
    return valor


async def _quien_tiene_el_cerrojo(conn: asyncpg.Connection) -> str:
    """Pid que lo retiene, para el mensaje de error; best-effort."""
    try:
        pid = await conn.fetchval(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND objsubid = 1 AND ((classid::bigint << 32) | objid::bigint) = $1 "
            "LIMIT 1",
            CERROJO_MIGRACIONES,
        )
    except Exception:  # noqa: BLE001 - diagnóstico; nunca debe tapar el error real
        return "desconocido"
    return str(pid) if pid is not None else "desconocido"


@asynccontextmanager
async def cerrojo(
    conn: asyncpg.Connection, *, timeout_s: float | None = None
) -> AsyncIterator[None]:
    """Serializa todo ejecutor de migraciones del sistema (runner e init_tenant).

    Intenta ``pg_try_advisory_lock`` cada medio segundo hasta ``timeout_s``
    (por defecto ``MIGRATIONS_LOCK_TIMEOUT_S``). Si vence, ``RuntimeError``
    legible: el proceso sale con código 1 y ``service_completed_successfully``
    no se cumple, así que el servicio NO arranca (fail-closed). Se libera en
    ``finally``.
    """
    plazo = plazo_cerrojo_s() if timeout_s is None else timeout_s
    inicio = time.monotonic()
    while not await conn.fetchval("SELECT pg_try_advisory_lock($1)", CERROJO_MIGRACIONES):
        espera = time.monotonic() - inicio
        if espera >= plazo:
            raise RuntimeError(
                f"otra migración tiene el cerrojo desde hace {espera:.0f} s "
                f"(pid {await _quien_tiene_el_cerrojo(conn)}); plazo "
                f"MIGRATIONS_LOCK_TIMEOUT_S={plazo:g} s agotado"
            )
        await asyncio.sleep(min(_ESPERA_ENTRE_INTENTOS_S, max(plazo - espera, 0.01)))
    try:
        yield
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", CERROJO_MIGRACIONES)
        except Exception:  # noqa: BLE001 - conexión ya caída: el servidor lo libera al cerrar la sesión
            pass


# `schema_name` of the migrations that go to `public`. Stored, not NULL: a NULL
# in a unique key does not compare equal to itself and uniqueness would be lost.
ESQUEMA_GLOBAL = "public"

_NUMBERED = re.compile(r"^\d+_")


async def ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            schema_name  TEXT NOT NULL,
            filename     TEXT NOT NULL,
            applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (schema_name, filename)
        )
        """
    )


async def applied(conn: asyncpg.Connection, schema_name: str) -> set[str]:
    """File names already applied in *schema_name*."""
    rows = await conn.fetch(
        "SELECT filename FROM public.schema_migrations WHERE schema_name = $1", schema_name
    )
    return {r["filename"] for r in rows}


async def record(conn: asyncpg.Connection, schema_name: str, filename: str) -> None:
    await conn.execute(
        "INSERT INTO public.schema_migrations (schema_name, filename) VALUES ($1, $2)",
        schema_name,
        filename,
    )


def sql_files(directory: Path) -> list[Path]:
    """The numbered `.sql` files of *directory*, in order."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.sql") if _NUMBERED.match(p.name))


async def tenant_schemas(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        r"""
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name LIKE 'tenant\_%' ESCAPE '\'
        ORDER BY schema_name
        """
    )
    return [r["schema_name"] for r in rows]


async def apply_chain(
    conn: asyncpg.Connection,
    directory: Path,
    schema_name: str,
    *,
    log: Callable[[str], None] = print,
) -> None:
    """Apply the pending files of *directory* to *schema_name*, one transaction each.

    The search path is the schema alone: a tenant file that forgets to qualify a
    `public` object fails here and not in production.
    """
    done = await applied(conn, schema_name)
    for path in sql_files(directory):
        if path.name in done:
            continue
        log(f"  apply [{schema_name}] {path.name}")
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{schema_name}"')
            await conn.execute(path.read_text(encoding="utf-8"))
            await record(conn, schema_name, path.name)


async def migrate(
    conn: asyncpg.Connection,
    migrations_dir: Path,
    *,
    log: Callable[[str], None] = print,
) -> None:
    """Bring `public` and every `tenant_*` schema up to date. Takes the lock."""
    async with cerrojo(conn):
        await ensure_ledger(conn)
        await apply_chain(conn, migrations_dir / "global", ESQUEMA_GLOBAL, log=log)
        for schema in await tenant_schemas(conn):
            await apply_chain(conn, migrations_dir / "tenant", schema, log=log)
