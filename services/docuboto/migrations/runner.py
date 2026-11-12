# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""The migration runner: `global/` to `public`, `tenant/` to every `tenant_*` schema."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from orpycamcp_common import migrations

MIGRATIONS_DIR = Path(__file__).parent


def dsn() -> str:
    raw = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError(
            "DATABASE_URL no está definida: el runner de migraciones necesita la "
            "cadena de conexión a PostgreSQL."
        )
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def run() -> None:
    conn = await asyncpg.connect(dsn=dsn())
    try:
        await migrations.migrate(conn, MIGRATIONS_DIR)
        print("migrations done")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
