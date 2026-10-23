# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) de la paginación de `GET /prestamos` (RF-ARF-08).

`list_prestamos` devolvía la tabla entera sin `LIMIT`. Los préstamos activos y
los vencidos están acotados por la operación —son los que alguien tiene encima de
la mesa—, pero `devuelto` es el HISTÓRICO: crece con cada préstamo que se cierra
y no deja de crecer.

Se prueba contra el motor porque lo que se añade son cláusulas SQL: un `COUNT`
que tiene que compartir el `WHERE` con las filas, un `LIMIT/OFFSET` y un orden
que tiene que ser TOTAL. Con un doble del repositorio, las tres pasarían
igual estando mal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.fisico_repository import FisicoRepository

from ._auth_minimo import crear_tablas_auth

pytestmark = pytest.mark.integration


async def _en_tenant(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    """El `search_path` se fija aquí para el SEMBRADO; el repositorio fija el suyo
    a partir del slug, así que hay que pasarle el REAL del fixture y no uno
    inventado — con un slug cualquiera apunta a un schema que no existe."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    await crear_tablas_auth(conn, slug)  # `list_prestamos` une `auth_users` (la crea auth-service)
    return slug, conn


async def _unidad(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO unidad_conservacion (tipo, codigo, signatura) "
        "VALUES ('caja', $1, $2) RETURNING id",
        f"C-{uuid.uuid4().hex[:6]}",
        f"SIG-{uuid.uuid4().hex[:8]}",
    )


async def _prestamo(
    conn: asyncpg.Connection,
    unidad_id: uuid.UUID,
    *,
    estado: str = "prestado",
    vence: date | None = None,
) -> uuid.UUID:
    """Siembra un préstamo respetando el esquema 072.

    Un préstamo VIVO por unidad (índice parcial `ux_prestamo_unidad_activo`): los
    `prestado` sembrados van cada uno en su unidad nueva (se ignora `unidad_id`);
    el histórico `devuelto` sí se acumula en la unidad indicada."""
    if estado == "prestado":
        unidad_id = await _unidad(conn)
        devolucion = (None, None)
    else:
        devolucion = (datetime.now(tz=UTC), uuid.uuid4())
    return await conn.fetchval(
        "INSERT INTO prestamo (unidad_id, prestatario_tipo, tercero_nombre, "
        "tercero_documento_tipo, tercero_documento_numero, prestado_por, "
        "fecha_devolucion_esperada, estado, fecha_devolucion_real, devuelto_recibido_por) "
        "VALUES ($1, 'externo', 'ANA PEREZ', 'CC', '1234567', $2, $3, $4, $5, $6) RETURNING id",
        unidad_id,
        uuid.uuid4(),
        vence or (date.today() + timedelta(days=10)),
        estado,
        devolucion[0],
        devolucion[1],
    )


@pytest.mark.asyncio
async def test_el_total_cuenta_todo_y_la_pagina_solo_su_ventana(tenant_ctx) -> None:
    """El recuento NO es `len(items)`: es lo que hay.

    Ese era el único total posible antes, y es el tamaño de la ventana haciéndose
    pasar por el de la consulta — justo la ventana que se quiere eliminar.
    """
    slug, conn = await _en_tenant(tenant_ctx)
    unidad = await _unidad(conn)
    for _ in range(7):
        await _prestamo(conn, unidad)

    items, total = await FisicoRepository(conn, slug).list_prestamos(
        estado="prestado", unidad_id=None, page=1, size=3
    )

    assert len(items) == 3
    assert total == 7


@pytest.mark.asyncio
async def test_el_total_comparte_el_filtro_con_las_filas(tenant_ctx) -> None:
    """Control positivo: un `COUNT` sin el `WHERE` anunciaría páginas inexistentes."""
    slug, conn = await _en_tenant(tenant_ctx)
    unidad = await _unidad(conn)
    for _ in range(4):
        await _prestamo(conn, unidad, estado="prestado")
    for _ in range(9):
        await _prestamo(conn, unidad, estado="devuelto")

    _, total_prestados = await FisicoRepository(conn, slug).list_prestamos(
        estado="prestado", unidad_id=None, page=1, size=50
    )
    _, total_devueltos = await FisicoRepository(conn, slug).list_prestamos(
        estado="devuelto", unidad_id=None, page=1, size=50
    )

    assert total_prestados == 4
    assert total_devueltos == 9


@pytest.mark.asyncio
async def test_los_empates_se_resuelven_por_un_orden_TOTAL(tenant_ctx) -> None:
    """Con la fecha empatada, la secuencia la fija `id DESC` y no el azar.

    Los empates aquí no son raros, son lo normal: `fecha_prestamo` es
    `TIMESTAMPTZ DEFAULT now()` y `now()` devuelve el instante de INICIO DE LA
    TRANSACCIÓN, así que todo lo insertado en una misma transacción —un lote, un
    sembrado— comparte timestamp EXACTO. Sin una última clave única el `ORDER BY`
    es parcial, y paginar sobre él repetiría una fila y perdería otra sin
    síntoma: un préstamo que desaparece del listado sin que nadie lo note.

    Se mide comparando la secuencia contra `id DESC`, y no paginando: dos
    consultas con el mismo plan devuelven los empates en el mismo orden por
    casualidad, así que una prueba por páginas daría verde con desempate y sin
    él — comprobado, la mutación no caía.
    """
    slug, conn = await _en_tenant(tenant_ctx)
    unidad = await _unidad(conn)
    async with conn.transaction():  # mismo `now()` para los ocho
        sembrados = [await _prestamo(conn, unidad) for _ in range(8)]

    items, total = await FisicoRepository(conn, slug).list_prestamos(
        estado="prestado", unidad_id=None, page=1, size=50
    )

    assert total == 8
    assert [p["id"] for p in items] == sorted(sembrados, reverse=True)


@pytest.mark.asyncio
async def test_sin_paginacion_se_traen_todos(tenant_ctx) -> None:
    """Control de regresión: el listado POR UNIDAD llama sin `page`/`size`.

    Ahí la cota la pone el objeto —una unidad no acumula miles de préstamos—, así
    que se deja sin paginar a propósito. Si esta rama se rompiera, esa pantalla
    mostraría una ventana silenciosa, que es el defecto que se está cerrando.
    """
    slug, conn = await _en_tenant(tenant_ctx)
    unidad = await _unidad(conn)
    for _ in range(5):
        await _prestamo(conn, unidad, estado="devuelto")

    items, total = await FisicoRepository(conn, slug).list_prestamos(
        estado=None, unidad_id=unidad
    )

    assert len(items) == 5
    assert total == 5


@pytest.mark.asyncio
async def test_vencido_deja_fuera_al_que_vence_hoy(tenant_ctx) -> None:
    """La convención de préstamos es `< CURRENT_DATE`: hoy todavía está en plazo.

    Se fija aquí porque el proyecto usa la contraria en la TRD (`<=`), y esa
    discrepancia es una decisión de producto abierta: si algún día se unifica,
    este test tiene que caer y obligar a decidirlo a la vista, no en silencio.
    """
    slug, conn = await _en_tenant(tenant_ctx)
    unidad = await _unidad(conn)
    await _prestamo(conn, unidad, vence=date.today())
    await _prestamo(conn, unidad, vence=date.today() - timedelta(days=1))

    items, total = await FisicoRepository(conn, slug).list_prestamos(
        estado="vencido", unidad_id=None, page=1, size=50
    )

    assert total == 1
    assert items[0]["fecha_devolucion_esperada"] == date.today() - timedelta(days=1)


@pytest.mark.asyncio
async def test_los_prestamos_de_una_unidad_paginan_y_cuentan_solo_los_suyos(tenant_ctx) -> None:
    """AREA-07-19: el listado por unidad usa la misma paginación que el global.

    25 préstamos en la unidad A y 4 en la B: con `size=20` la A devuelve 20 y su
    total es 25 —no 29 ni 20—, y la segunda página trae los 5 restantes.
    """
    slug, conn = await _en_tenant(tenant_ctx)
    a = await _unidad(conn)
    b = await _unidad(conn)
    for _ in range(25):
        await _prestamo(conn, a, estado="devuelto")
    for _ in range(4):
        await _prestamo(conn, b, estado="devuelto")

    repo = FisicoRepository(conn, slug)
    p1, total = await repo.list_prestamos(estado=None, unidad_id=a, page=1, size=20)
    p2, total2 = await repo.list_prestamos(estado=None, unidad_id=a, page=2, size=20)

    assert (len(p1), total) == (20, 25)
    assert (len(p2), total2) == (5, 25)
    assert not {r["id"] for r in p1} & {r["id"] for r in p2}
