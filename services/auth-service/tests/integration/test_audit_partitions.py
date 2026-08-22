# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Particionado mensual de `audit_log` (11.4), contra Postgres REAL.

Carril de integración obligado: lo que hay que comprobar es el comportamiento de
PostgreSQL con particiones y triggers —dónde aterriza una fila, qué rechaza cada
tabla—, y ninguna de esas dos cosas se puede afirmar con un mock.

El riesgo que cubren estos tests no es que falten particiones: es que **existan
sin endurecer**. Los guards de inmutabilidad de 003/004 se aplican tabla por
tabla, y una partición recién creada no los hereda. Un job que crease
particiones mensuales sin llamar a `harden_audit_partition` abriría, mes a mes,
un agujero en el registro cuya inmutabilidad sostiene el valor probatorio de
todo el archivo — y no daría ningún síntoma hasta que alguien lo aprovechara.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


# Hash con la FORMA que exige `chk_audit_log_hash_forma` (migración global 006):
# SHA-256 en 64 hex. Estos tests insertan por SQL a propósito —comprueban el
# enrutado a la partición y su inmutabilidad, no el encadenado—, pero un hash de
# juguete ya no entra, y con razón: escribir basura en el registro legal es
# justamente lo que esa restricción vino a impedir tras encontrarse 25 filas
# contaminadas en los tenants `demo` y `otra-entidad`, imposibles de borrar
# porque el registro es append-only.
_HASH_VALIDO = "0" * 64


def _mes_libre(offset_meses: int = 60) -> tuple[str, datetime]:
    """Un mes muy por delante, para no chocar con las particiones reales."""
    base = datetime.now(UTC).replace(day=1, hour=12, minute=0, second=0, microsecond=0)
    anio = base.year + (base.month - 1 + offset_meses) // 12
    mes = (base.month - 1 + offset_meses) % 12 + 1
    return f"audit_log_{anio:04d}_{mes:02d}", base.replace(year=anio, month=mes, day=15)


@pytest.fixture
async def particion_desechable(db_conn: asyncpg.Connection):
    nombre, momento = _mes_libre()
    inicio = momento.replace(day=1)
    fin = (inicio + timedelta(days=32)).replace(day=1)
    await db_conn.execute(
        f"CREATE TABLE public.{nombre} PARTITION OF public.audit_log "
        f"FOR VALUES FROM ('{inicio.date()}') TO ('{fin.date()}')"
    )
    await db_conn.execute("SELECT public.harden_audit_partition($1)", nombre)
    yield nombre, momento
    # `DROP` de la partición entera: no se puede borrar la fila (append-only,
    # que es justamente lo que este test comprueba), así que se retira el
    # contenedor. La tabla madre y el histórico quedan intactos.
    await db_conn.execute(f"DROP TABLE public.{nombre}")


async def test_el_asiento_aterriza_en_su_particion_mensual(
    db_conn: asyncpg.Connection, particion_desechable
):
    nombre, momento = particion_desechable
    slug = f"itest-part-{momento:%Y%m}"

    await db_conn.execute(
        "INSERT INTO public.audit_log (tenant_slug, ts, service, action, object_type, hash) "
        "VALUES ($1, $2, 'test', 'x', 'y', $3)",
        slug,
        momento,
        _HASH_VALIDO,
    )

    destino = await db_conn.fetchval(
        "SELECT tableoid::regclass::text FROM public.audit_log WHERE tenant_slug = $1", slug
    )
    assert destino == nombre, "el asiento no fue a su partición mensual"


async def test_la_particion_nueva_es_append_only(
    db_conn: asyncpg.Connection, particion_desechable
):
    """El agujero que se abriría mes a mes si el job no endureciera lo que crea."""
    nombre, momento = particion_desechable
    slug = f"itest-immut-{momento:%Y%m}"
    await db_conn.execute(
        "INSERT INTO public.audit_log (tenant_slug, ts, service, action, object_type, hash) "
        "VALUES ($1, $2, 'test', 'x', 'y', $3)",
        slug,
        momento,
        _HASH_VALIDO,
    )

    for sentencia in (
        f"UPDATE public.{nombre} SET action = 'mutado'",
        f"DELETE FROM public.{nombre}",
        f"TRUNCATE public.{nombre}",
    ):
        with pytest.raises(asyncpg.PostgresError):
            await db_conn.execute(sentencia)


async def test_ensure_audit_partitions_es_idempotente(db_conn: asyncpg.Connection):
    """Se llama en cada despliegue y desde un cron: si duplicara o fallara al
    encontrarse lo suyo, el job no se podría automatizar."""
    primera = await db_conn.fetch("SELECT * FROM public.ensure_audit_partitions(2)")
    segunda = await db_conn.fetch("SELECT * FROM public.ensure_audit_partitions(2)")

    assert {f["particion"] for f in primera} == {f["particion"] for f in segunda}
    assert all(not f["creada"] for f in segunda), "la segunda pasada volvió a crear"


async def test_no_intenta_crear_meses_que_solapan_con_el_default(db_conn: asyncpg.Connection):
    """PostgreSQL rechaza una partición cuyo rango solape con filas del default.

    La función arranca en el primer mes libre en vez de fallar con «updated
    partition constraint for default partition would be violated by some row»,
    que es un mensaje correcto y completamente opaco. Sin esta regla, el job
    reventaría en cualquier despliegue con histórico — es decir, en todos los que
    ya están en producción, que son los únicos donde importa.
    """
    ultimo = await db_conn.fetchval("SELECT max(ts) FROM public.audit_log_default")
    creadas = await db_conn.fetch("SELECT * FROM public.ensure_audit_partitions(2)")

    if ultimo is not None:
        primera = min(f["particion"] for f in creadas)
        assert primera > f"audit_log_{ultimo:%Y_%m}", (
            "se intentó particionar un mes que el default todavía ocupa"
        )
