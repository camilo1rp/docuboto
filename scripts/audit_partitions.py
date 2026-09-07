# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
audit_partitions.py — crea las particiones mensuales de `public.audit_log`.

La lógica vive en la función SQL `public.ensure_audit_partitions(n)`
(`services/auth-service/migrations/global/005_audit_partition_job.sql`), que es
donde tiene que estar: así el mismo comportamiento vale desde una migración,
desde `psql` y desde este script, y no hay dos versiones que puedan divergir.

Uso (desde el host, con la infraestructura levantada):

    docker compose run --rm --no-deps \\
        -v "$PWD/scripts:/scripts:ro" \\
        auth-service python /scripts/audit_partitions.py --meses 3

En producción, un cron mensual. Se adelanta a propósito varios meses: si el cron
falla en silencio, hay margen antes de que los asientos vuelvan a caer en la
partición por defecto — y volver a sacarlos de ahí es justamente lo caro.

Salida con el estado de cada partición y código 0 si todo fue bien; el modo
`--verificar` no crea nada y devuelve 1 si el mes en curso o el siguiente no
tienen partición, para poder alertar desde la supervisión.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

import asyncpg


def _dsn() -> str:
    crudo = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not crudo:
        print(
            "ERROR: DATABASE_URL no está definida. Ej.: "
            "DATABASE_URL=postgresql+asyncpg://usuario:clave@postgres:5432/base",
            file=sys.stderr,
        )
        sys.exit(2)
    return crudo.replace("postgresql+asyncpg://", "postgresql://")


def _meses_esperados(cuantos: int) -> list[str]:
    hoy = datetime.now(UTC)
    nombres = []
    anio, mes = hoy.year, hoy.month
    for _ in range(cuantos + 1):
        nombres.append(f"audit_log_{anio:04d}_{mes:02d}")
        mes += 1
        if mes == 13:
            anio, mes = anio + 1, 1
    return nombres


async def crear(conn: asyncpg.Connection, meses: int) -> int:
    filas = await conn.fetch("SELECT * FROM public.ensure_audit_partitions($1)", meses)
    for fila in filas:
        print(f"  {'crea ' if fila['creada'] else 'ok   '} {fila['particion']}")
    creadas = sum(1 for f in filas if f["creada"])
    print(f"particiones: {len(filas)} vigentes, {creadas} nuevas")
    return 0


async def verificar(conn: asyncpg.Connection) -> int:
    """¿Está cubierto el mes SIGUIENTE?

    Ese es el criterio accionable, no el mes en curso. Un despliegue que acaba
    de instalar el job arrastra su histórico en `audit_log_default`, y mientras
    ahí haya filas del mes actual PostgreSQL no deja crear su partición: alertar
    por eso sería alertar por una situación que se resuelve sola el día 1 y que
    nadie puede arreglar. Lo que sí significa que el cron dejó de correr es que
    falte el mes que viene, y eso avisa con semanas de margen.
    """
    existentes = {
        f["relname"]
        for f in await conn.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname LIKE 'audit_log\\_%'"
        )
    }
    actual, siguiente = _meses_esperados(1)

    recientes = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log_default WHERE ts >= date_trunc('month', now())"
    )
    if actual not in existentes and recientes:
        print(
            f"aviso: {recientes} asientos de este mes están en audit_log_default "
            f"(arrastre histórico; {actual} no se puede crear hasta que ese rango quede libre)"
        )

    if siguiente not in existentes:
        print(
            f"FALTA la partición del mes siguiente ({siguiente}): el job no está corriendo",
            file=sys.stderr,
        )
        return 1

    print("particiones al día")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meses", type=int, default=3, help="meses por delante a cubrir")
    parser.add_argument(
        "--verificar", action="store_true", help="solo comprobar; no crea nada (para alertas)"
    )
    args = parser.parse_args()

    conn = await asyncpg.connect(dsn=_dsn())
    try:
        return await (verificar(conn) if args.verificar else crear(conn, args.meses))
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
