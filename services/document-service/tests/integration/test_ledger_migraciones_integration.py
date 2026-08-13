# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Libro único de migraciones (11.3), contra Postgres REAL.

Va en el carril de integración y no en el unitario a propósito: lo que hay que
probar es la CONVIVENCIA con lo que ya está escrito en una base existente —tres
registros con dos convenciones de clave—, y eso un mock no lo puede afirmar.
Es la misma lección de `tier-integracion-atomicidad`: la garantía que importa
aquí es sobre datos que ya existen, no sobre llamadas.

Escenario reproducido: una base provisionada con el sistema ANTIGUO, donde

  - `public._migrations` guarda nombres desnudos (runner) y `servicio/archivo`
    (init_tenant), mezclados;
  - `public._tenant_migrations` guarda nombres desnudos por esquema (runner);
  - `tenant_X._migrations` guarda `servicio/archivo` (init_tenant).

Lo que se comprueba: tras el import, nada se reaplica, y la migración que el
nombre desnudo hacía saltar en silencio queda pendiente.
"""

from __future__ import annotations

import uuid

import pytest
from orpycamcp_common import migrations as ledger

pytestmark = pytest.mark.asyncio

# Nombres reales del repositorio. `002_batch_jobs.sql` lo declaran archive Y
# document; los tres de dead-letter, notification Y signature. Son las cuatro
# colisiones que hacen que el nombre desnudo no identifique una migración.
MAPA_TENANT = {
    "001_solo_de_document.sql": ["document-service"],
    "002_batch_jobs.sql": ["archive-service", "document-service"],
    "005_evento_dead_letter.sql": ["notification-service", "signature-service"],
}
MAPA_GLOBAL = {"001_extensions.sql": ["document-service"]}


@pytest.fixture
async def base_antigua(db_conn):
    """Deja la base como la dejaba el sistema de tres registros, y la limpia."""
    esquema = f"tenant_ledger_{uuid.uuid4().hex[:8]}"
    await db_conn.execute(f'CREATE SCHEMA "{esquema}"')
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS public._migrations (
            id SERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now())
    """)
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS public._tenant_migrations (
            id SERIAL PRIMARY KEY, schema_name TEXT NOT NULL, filename TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (schema_name, filename))
    """)
    await db_conn.execute(f"""
        CREATE TABLE "{esquema}"._migrations (
            id SERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now())
    """)

    # (A) runner, global, nombre desnudo.
    await db_conn.execute(
        "INSERT INTO public._migrations (filename) VALUES ('001_extensions.sql') "
        "ON CONFLICT DO NOTHING"
    )
    # (B) runner, tenant, nombre desnudo. El ambiguo lo escribió UNO de los dos
    #     servicios y el otro hizo skip; el registro no dice cuál.
    for archivo in ("001_solo_de_document.sql", "002_batch_jobs.sql"):
        await db_conn.execute(
            "INSERT INTO public._tenant_migrations (schema_name, filename) VALUES ($1, $2)",
            esquema,
            archivo,
        )
    # (C) init_tenant, tenant, clave servicio/archivo.
    await db_conn.execute(
        f'INSERT INTO "{esquema}"._migrations (filename) '
        "VALUES ('notification-service/005_evento_dead_letter.sql')"
    )

    yield esquema

    await db_conn.execute(f'DROP SCHEMA "{esquema}" CASCADE')
    await db_conn.execute(
        "DELETE FROM public._tenant_migrations WHERE schema_name = $1", esquema
    )
    await db_conn.execute(
        "DELETE FROM public.schema_migrations WHERE schema_name = $1", esquema
    )


async def _importar(db_conn):
    await ledger.ensure_ledger(db_conn)
    return await ledger.importar_libros_antiguos(
        db_conn,
        servicios_por_archivo_global=MAPA_GLOBAL,
        servicios_por_archivo_tenant=MAPA_TENANT,
        # Con los mapas recortados de este test, cualquier migración REAL que ya
        # esté en la base —y en una base de desarrollo hay cientos— caería en la
        # rama "ningún servicio lo declara" y se importaría como
        # `(desconocido)`. La primera versión de este test hizo exactamente eso
        # y dejó 32 filas basura en el libro: el módulo se comportó como está
        # escrito, el que mentía sobre su alcance era el test.
        ignorar_desconocidos=True,
    )


async def test_lo_ya_aplicado_por_ambos_ejecutores_no_se_reaplica(db_conn, base_antigua):
    """El fallo que corrige: los dos ejecutores no se veían, así que cada uno
    reaplicaba lo del otro. Con `CREATE TABLE` idempotente no se nota; con un
    `INSERT` de datos semilla o un `ALTER`, sí."""
    await _importar(db_conn)

    en_global = await ledger.applied(db_conn, ledger.ESQUEMA_GLOBAL)
    en_tenant = await ledger.applied(db_conn, base_antigua)

    assert ("document-service", "001_extensions.sql") in en_global
    assert ("document-service", "001_solo_de_document.sql") in en_tenant
    assert ("notification-service", "005_evento_dead_letter.sql") in en_tenant


async def test_el_nombre_ambiguo_no_se_da_por_aplicado_para_los_dos(db_conn, base_antigua):
    """El corazón del defecto: con la clave desnuda, el segundo servicio veía el
    nombre y saltaba una migración SUYA que nunca se había aplicado.

    Se atribuye a un solo servicio y la del otro queda pendiente: reaplicar una
    idempotente no cuesta nada, dar por aplicada una que no lo está deja el
    esquema incompleto para siempre.
    """
    avisos = await _importar(db_conn)
    en_tenant = await ledger.applied(db_conn, base_antigua)

    atribuidos = {s for s, f in en_tenant if f == "002_batch_jobs.sql"}
    assert atribuidos == {"archive-service"}
    assert any("002_batch_jobs.sql" in a for a in avisos), "la ambigüedad se silenció"


async def test_el_import_es_idempotente(db_conn, base_antigua):
    """Se ejecuta en cada arranque de los dos ejecutores: si duplicara, el libro
    crecería sin límite y `applied` se volvería más lento en cada despliegue."""
    await _importar(db_conn)
    antes = await db_conn.fetchval(
        "SELECT count(*) FROM public.schema_migrations WHERE schema_name = $1", base_antigua
    )

    await _importar(db_conn)
    despues = await db_conn.fetchval(
        "SELECT count(*) FROM public.schema_migrations WHERE schema_name = $1", base_antigua
    )

    assert antes == despues


async def test_la_identidad_incluye_el_servicio(db_conn, base_antigua):
    """Dos servicios pueden tener el MISMO nombre de archivo en el MISMO esquema
    sin pisarse. Es la propiedad que el libro antiguo no tenía."""
    await ledger.ensure_ledger(db_conn)
    await ledger.record(db_conn, base_antigua, "archive-service", "002_batch_jobs.sql")
    await ledger.record(db_conn, base_antigua, "document-service", "002_batch_jobs.sql")

    en_tenant = await ledger.applied(db_conn, base_antigua)
    assert ("archive-service", "002_batch_jobs.sql") in en_tenant
    assert ("document-service", "002_batch_jobs.sql") in en_tenant
