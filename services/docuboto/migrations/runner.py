# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""The migration runner: `global/` to `public`, `tenant/` to every `tenant_*` schema.

After the chain it records the image's `ORPYCA_VERSION` in `public.schema_version`
(ADR-053 §3): the version is baked in at build time, `dev` when built locally.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from orpycamcp_common import migrations

MIGRATIONS_DIR = Path(__file__).parent

RECORD_VERSION = """
INSERT INTO public.schema_version (version) VALUES ($1)
ON CONFLICT (version) DO UPDATE SET applied_at = now()
"""


def version() -> str:
    return os.environ.get("ORPYCA_VERSION", "").strip() or "dev"


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
        await conn.execute(RECORD_VERSION, version())
        print(f"migrations done ({version()})")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
