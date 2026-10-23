# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) de los filtros de plazo y estados de `GET /documents`.

Por qué contra el motor y no con mocks: lo que se añade son cláusulas SQL —un
`ANY($n::text[])`, dos comparaciones de fecha y un `ORDER BY ... NULLS LAST`—.
Un mock del repositorio comprobaría que el router pasa los argumentos, que es
exactamente lo que no falla nunca; que el `WHERE` selecciona lo que dice solo lo
puede contestar Postgres.

El caso que motiva todo: el listado ordenaba **siempre** por `registered_at DESC`,
así que un radicado vencido y ANTIGUO —el que de verdad importa en un informe de
vencimientos— caía fuera de las primeras páginas por construcción. Se podía
pedir, pero no alcanzar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository

pytestmark = pytest.mark.integration

PUBLICA = 1
CLASIFICADA = 3

SIN_RESTRICCION = 3


async def _en_tenant(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    return slug, conn


async def _seed(
    conn: asyncpg.Connection,
    *,
    asunto: str,
    status: str = "registered",
    due: date | None = None,
    nivel: int = PUBLICA,
    registrado: str = "2026-09-01",
) -> uuid.UUID:
    """Siembra un radicado con su plazo y su fecha de radicación explícitas.

    `registered_at` se fija a mano porque el orden por defecto depende de él: si
    todos nacieran «ahora» no habría forma de distinguir el orden nuevo del viejo.
    """
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, "
        "tenant_code, nivel_seguridad, status, due_date, registered_at, "
        "clasificacion_fundamento, clasificacion_causal) "
        "VALUES ($1, 'E', 2026, 1, $2, 'DEMO', $3, $4, $5, $6, "
        "CASE WHEN $3::smallint >= 2 THEN 'Ley 1712/2014 art. 19 (prueba)' END, "
        "CASE WHEN $3::smallint >= 2 THEN 'por_clasificar' END) RETURNING id",
        f"2026-DEMO-E-{uuid.uuid4().hex[:6]}",
        asunto,
        nivel,
        status,
        due,
        datetime.fromisoformat(f"{registrado}T10:00:00").replace(tzinfo=UTC),
    )


async def _listar(conn: asyncpg.Connection, **kwargs):
    """Atajo con los argumentos que este fichero no varía."""
    base = dict(
        doc_type=None,
        status=None,
        metadata_filter=None,
        page=1,
        size=20,
        max_clearance=SIN_RESTRICCION,
    )
    base.update(kwargs)
    return await DocumentRepository(conn, "itest").find_all(**base)


# ---------------------------------------------------------------------------
# Varios estados en una sola consulta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_varios_estados_traen_los_de_todos_ellos(tenant_ctx) -> None:
    """El caso que antes exigía una llamada por estado y sumar a mano.

    Sumar dos ventanas de 20 en el cliente no da las 40 más urgentes: da 20 de
    cada una, y ninguna de las dos listas se puede ordenar respecto de la otra.
    """
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="EN REPARTO", status="distributed")
    await _seed(conn, asunto="EN TRÁMITE", status="in_progress")
    await _seed(conn, asunto="CERRADO", status="closed")

    docs, total = await _listar(conn, status=["distributed", "in_progress"])

    assert {d.subject for d in docs} == {"EN REPARTO", "EN TRÁMITE"}
    # El total acompaña al filtro: el `COUNT` comparte el mismo `WHERE`, o el
    # paginador prometería páginas que no existen.
    assert total == 2


@pytest.mark.asyncio
async def test_un_solo_estado_se_comporta_igual_que_antes(tenant_ctx) -> None:
    """Control positivo: la forma que usan hoy todas las pantallas no cambia."""
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="EN REPARTO", status="distributed")
    await _seed(conn, asunto="EN TRÁMITE", status="in_progress")

    docs, total = await _listar(conn, status=["distributed"])

    assert [d.subject for d in docs] == ["EN REPARTO"]
    assert total == 1


@pytest.mark.asyncio
async def test_sin_estado_no_filtra_nada(tenant_ctx) -> None:
    """Control de vacuidad: una lista vacía no puede comportarse como un filtro.

    `if status:` trata `None` y `[]` igual a propósito — pedir «ninguno de estos
    estados» devolviendo cero filas sería una lectura defendible, pero no es la
    que ningún cliente hace, y el riesgo es el contrario: un filtro construido
    con una lista vacía dejaría la bandeja en blanco sin decir por qué.
    """
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="UNO", status="distributed")
    await _seed(conn, asunto="DOS", status="closed")

    assert (await _listar(conn, status=None))[1] == 2
    assert (await _listar(conn, status=[]))[1] == 2


# ---------------------------------------------------------------------------
# Rango de fecha límite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_rango_de_plazo_incluye_los_dos_extremos(tenant_ctx) -> None:
    """Ambos extremos INCLUSIVOS, y por eso «vencido» lo declara quien pregunta.

    El proyecto usa hoy las dos convenciones opuestas —la TRD cuenta vencido el
    día en que se cumple el plazo, los préstamos no—. Con el rango inclusivo las
    dos se pueden pedir sin que el endpoint decida por nadie.
    """
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="ANTES", due=date(2026, 9, 9))
    await _seed(conn, asunto="BORDE BAJO", due=date(2026, 9, 10))
    await _seed(conn, asunto="DENTRO", due=date(2026, 9, 15))
    await _seed(conn, asunto="BORDE ALTO", due=date(2026, 9, 20))
    await _seed(conn, asunto="DESPUÉS", due=date(2026, 9, 21))

    docs, total = await _listar(conn, due_from=date(2026, 9, 10), due_to=date(2026, 9, 20))

    assert {d.subject for d in docs} == {"BORDE BAJO", "DENTRO", "BORDE ALTO"}
    assert total == 3


@pytest.mark.asyncio
async def test_el_radicado_sin_plazo_no_entra_en_ningun_rango(tenant_ctx) -> None:
    """Sin `due_date` la pregunta no le aplica: no es urgente ni holgado.

    Importa decirlo porque un `NULL` que se colara en «vencidos» inflaría un
    informe con radicados a los que nadie les puso plazo.
    """
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="SIN PLAZO", due=None)
    await _seed(conn, asunto="CON PLAZO", due=date(2026, 9, 15))

    docs, _ = await _listar(conn, due_to=date(2026, 12, 31))

    assert [d.subject for d in docs] == ["CON PLAZO"]


# ---------------------------------------------------------------------------
# El orden: el defecto que ningún filtro arregla
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_vencido_mas_antiguo_llega_a_la_primera_pagina(tenant_ctx) -> None:
    """El caso por el que existe este fichero.

    Con `ORDER BY registered_at DESC` fijo, el radicado que lleva más tiempo
    vencido es el que más lejos queda de la primera página: se radicó antes, así
    que aparece después. Filtrar no lo arregla —sigue habiendo más de `size`
    resultados—; hace falta poder ordenar por el plazo.
    """
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="VIEJO Y VENCIDO", due=date(2026, 1, 15), registrado="2026-01-02")
    await _seed(conn, asunto="RECIENTE", due=date(2026, 12, 20), registrado="2026-09-15")
    await _seed(conn, asunto="INTERMEDIO", due=date(2026, 6, 10), registrado="2026-05-01")

    por_defecto, _ = await _listar(conn)
    assert por_defecto[0].subject == "RECIENTE"  # el orden viejo, intacto

    por_plazo, _ = await _listar(conn, order_by="due_date", order_dir="asc")
    assert [d.subject for d in por_plazo] == ["VIEJO Y VENCIDO", "INTERMEDIO", "RECIENTE"]


@pytest.mark.asyncio
async def test_el_sin_plazo_va_al_final_en_los_dos_sentidos(tenant_ctx) -> None:
    """`NULLS LAST` en ascendente y en descendente.

    La rama que de verdad mide algo es la DESCENDENTE: el default de Postgres es
    `NULLS LAST` para `ASC` y `NULLS FIRST` para `DESC`, así que sin la cláusula
    explícita «los de plazo más lejano» abriría con los radicados que no tienen
    plazo ninguno. Se comprobó por mutación, y de paso cazó un comentario mío que
    afirmaba lo contrario: la rama ascendente NO cae al quitarle el `NULLS LAST`,
    porque ahí es redundante.
    """
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="SIN PLAZO", due=None)
    await _seed(conn, asunto="TEMPRANO", due=date(2026, 3, 1))
    await _seed(conn, asunto="TARDE", due=date(2026, 11, 1))

    asc, _ = await _listar(conn, order_by="due_date", order_dir="asc")
    assert [d.subject for d in asc] == ["TEMPRANO", "TARDE", "SIN PLAZO"]

    desc, _ = await _listar(conn, order_by="due_date", order_dir="desc")
    assert [d.subject for d in desc] == ["TARDE", "TEMPRANO", "SIN PLAZO"]


@pytest.mark.asyncio
async def test_los_empates_se_resuelven_por_un_orden_TOTAL(tenant_ctx) -> None:
    """Con el plazo empatado, la secuencia la fija `id DESC` y no el azar.

    `due_date` es una fecha sin hora: los empates son la norma. Sin una última
    clave única el `ORDER BY` es PARCIAL, y un orden parcial resuelto por un
    ordenamiento inestable puede devolver los empates en distinto orden en dos
    consultas iguales. Al paginar eso significa un radicado repetido en una
    página y otro que no aparece en ninguna — un radicado que se cae del informe
    sin que nadie lo note.

    Se mide así, y no paginando, a propósito: el mismo plan devuelve el mismo
    orden por casualidad, de modo que una prueba por páginas daría verde con
    desempate y sin él. Comparar contra `id DESC` sí cae al quitarlo, porque el
    orden físico de inserción no coincide con el de unos UUID aleatorios.
    """
    _, conn = await _en_tenant(tenant_ctx)
    mismo_dia = date(2026, 4, 1)
    sembrados = [await _seed(conn, asunto=f"EMPATE {i}", due=mismo_dia) for i in range(8)]

    docs, total = await _listar(conn, order_by="due_date", order_dir="asc")

    assert total == 8
    assert [d.id for d in docs] == sorted(sembrados, reverse=True)


# ---------------------------------------------------------------------------
# El gate no se cae por el camino
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_los_filtros_nuevos_no_abren_una_puerta_al_clasificado(tenant_ctx) -> None:
    """El no-read-up sigue en el MISMO `WHERE` que los filtros (RF-SEG-08).

    Un filtro nuevo es una ocasión clásica de fuga: basta con que la condición de
    clearance se quede fuera de la rama nueva. Se comprueba en las filas **y en
    el total**, porque un recuento que cuenta lo que no se puede ver ya dice
    cuántos radicados clasificados existen.
    """
    _, conn = await _en_tenant(tenant_ctx)
    await _seed(conn, asunto="PÚBLICO", due=date(2026, 5, 5), nivel=PUBLICA)
    await _seed(conn, asunto="RESERVADO", due=date(2026, 5, 5), nivel=CLASIFICADA)

    docs, total = await _listar(
        conn,
        max_clearance=PUBLICA,
        due_from=date(2026, 1, 1),
        due_to=date(2026, 12, 31),
        order_by="due_date",
        order_dir="asc",
    )

    assert [d.subject for d in docs] == ["PÚBLICO"]
    assert total == 1
