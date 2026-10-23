# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Aplica migraciones SQL pendientes (ADR-012).

Dos clases por convención de carpetas:
    migrations/global/   → schema public
    migrations/tenant/   → cada schema tenant_{slug}

Ambas se anotan en el **libro único** `public.schema_migrations`, con la
identidad completa (esquema + servicio + archivo). Antes había tres registros
que no se veían entre sí y la clave era el nombre desnudo del archivo, que NO
identifica una migración: cada servicio numera su serie desde 001, y con cuatro
nombres repetidos entre archive/document y notification/signature el runner del
segundo servicio hacía *skip* de una migración suya que nunca se había
aplicado. Ver `orpycamcp_common.migrations` para el diagnóstico completo.

Uso:
    python migrations/runner.py

Lee DATABASE_URL del entorno. Idempotente y re-ejecutable.
"""

import asyncio
import os
import re
from pathlib import Path

import asyncpg
from orpycamcp_common import migrations as ledger

# Nombre del servicio dueño de estas migraciones. Es lo único que distingue a un
# runner de otro: la lógica vive entera en `orpycamcp_common.migrations`. Dentro
# de la imagen el código está en `/app`, así que el nombre no se puede derivar
# del path — y adivinarlo mal reintroduce justamente el defecto de identidad que
# este libro corrige.
SERVICIO = "knowledge-service"

MIGRATIONS_DIR = Path(__file__).parent
GLOBAL_DIR = MIGRATIONS_DIR / "global"
TENANT_DIR = MIGRATIONS_DIR / "tenant"
# Se lee PEREZOSAMENTE, no con `os.environ[...]` a nivel de módulo. Importar este
# runner no debe exigir que `DATABASE_URL` exista: el conftest del carril de
# integración lo importa para reutilizar `_sql_files`/`run`, y en el carril
# UNITARIO (mockeado, `--no-deps`) esa variable no está definida — la colección
# entera de tests moría con un `KeyError: 'DATABASE_URL'` pelado, sin decir de
# dónde venía. Al usarlo sí se exige, y con un mensaje que explica qué falta.
# Las migraciones se aplican con el rol PROPIETARIO, no con el de la aplicación.
#
# Crear tablas, índices y claves foráneas exige privilegios que el rol de la
# aplicación no tiene ni debe tener: ése es justamente el punto de separarlos.
# `MIGRATIONS_DATABASE_URL` es la conexión del propietario y tiene preferencia;
# si no está, se cae a `DATABASE_URL`, que es como funcionaba antes y sigue
# valiendo en desarrollo y en el carril de pruebas.
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""


def _require_dsn() -> str:
    if not DSN:
        raise RuntimeError(
            "DATABASE_URL no está definida: el runner de migraciones necesita la "
            "cadena de conexión a PostgreSQL. Ej.: "
            "DATABASE_URL=postgresql+asyncpg://usuario:clave@postgres:5432/base"
        )
    return DSN


def _sql_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(f for f in directory.glob("*.sql") if re.match(r"^\d+_", f.name))


def _mapa_propio(directory: Path) -> dict[str, list[str]]:
    """`{archivo: [este servicio]}` — lo único que este runner puede afirmar.

    Dentro de su imagen no existen los directorios de los demás servicios, así
    que no puede atribuirles nada: sus entradas antiguas las importará el runner
    de cada uno (de ahí `ignorar_desconocidos=True`).
    """
    return {p.name: [SERVICIO] for p in _sql_files(directory)}


async def _apply_global(conn: asyncpg.Connection) -> None:
    files = _sql_files(GLOBAL_DIR)
    if not files:
        return
    aplicadas = await ledger.applied(conn, ledger.ESQUEMA_GLOBAL)
    await conn.execute("SET search_path = public")
    for path in files:
        if (SERVICIO, path.name) in aplicadas:
            print(f"  skip  [global] {path.name}")
            continue
        print(f"  apply [global] {path.name}")
        # En transacción (C19): un fallo a mitad ya no deja DDL aplicado sin asiento
        # en el libro. Ningún .sql global usa CONCURRENTLY/ADD VALUE/VACUUM/BEGIN
        # (lo fija shared/tests/test_migraciones_al_arrancar.py).
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
        for path in files:
            if (SERVICIO, path.name) in aplicadas:
                print(f"  skip  [{schema}] {path.name}")
                continue
            print(f"  apply [{schema}] {path.name}")
            # schema viene de information_schema (creado por tenant-service con slug
            # validado); seguro para interpolar como identificador.
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}"')
                await conn.execute(path.read_text())
                await ledger.record(conn, schema, SERVICIO, path.name)
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
    conn = await asyncpg.connect(dsn=_require_dsn())
    try:
        # Cerrojo global único (C19, ADR-041): TODO lo que escribe el libro, `public`
        # o un esquema tenant corre serializado entre los 9 runners e init_tenant.
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
