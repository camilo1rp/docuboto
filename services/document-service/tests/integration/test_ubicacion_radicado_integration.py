# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-029 contra Postgres REAL, en document-service: las columnas de ubicación.

Lo que un mock no puede probar: que la columna GENERADA `ubicacion_*` se calcula
por PARES en el motor, que nadie puede escribirla, que la migración 037 es
idempotente y que el filtro por ubicación pone el no-read-up en el `WHERE` del
`SELECT` y del `COUNT` (el total no delata filas que la página no puede mostrar).

`flujo_*` las escribe workflow-service: aquí se simulan con un `UPDATE` directo.
"""

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository
from app.repositories.export_repository import ExportRepository
from app.repositories.report_repository import ReportRepository
from migrations import runner

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PUBLICA, CLASIFICADA = 1, 3


async def _en_tenant(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    return slug, conn


async def _rad(
    conn: asyncpg.Connection,
    *,
    dest: tuple[str, int] | None = None,
    origin: tuple[str, int] | None = None,
    flujo: tuple[str, int | None, uuid.UUID | None] | None = None,
    nivel: int = PUBLICA,
    status: str = "registered",
) -> uuid.UUID:
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code, "
        "nivel_seguridad, status, dest_dept, dest_dept_code, origin_dept, origin_dept_code) "
        "VALUES ($1, 'E', 2026, 1, 'ASUNTO', 'DEMO', $2, $3, $4, $5, $6, $7) RETURNING id",
        f"2026-DEMO-E-{uuid.uuid4().hex[:6]}",
        nivel, status,
        dest[0] if dest else None, dest[1] if dest else None,
        origin[0] if origin else None, origin[1] if origin else None,
    )
    if flujo is not None:
        await conn.execute(
            "UPDATE radicados SET flujo_dept = $2, flujo_dept_code = $3, flujo_usuario = $4 WHERE id = $1",
            rid, *flujo,
        )
    return rid


async def _ubic(conn: asyncpg.Connection, rid: uuid.UUID) -> tuple[str | None, int | None]:
    r = await conn.fetchrow("SELECT ubicacion_dept, ubicacion_dept_code FROM radicados WHERE id = $1", rid)
    return r["ubicacion_dept"], r["ubicacion_dept_code"]


async def test_la_ubicacion_generada_se_calcula_por_pares(tenant_ctx) -> None:
    _, conn = await _en_tenant(tenant_ctx)
    u = uuid.uuid4()
    solo_destino = await _rad(conn, dest=("Tecnología", 300), origin=("Origen", 100))
    solo_origen = await _rad(conn, origin=("Origen", 100))
    movido = await _rad(conn, dest=("Tecnología", 300), flujo=("Control Interno", 500, u))
    # Nombre del flujo SIN código único: el código es NULL y NO cae al de radicación.
    sin_codigo = await _rad(conn, dest=("Tecnología", 300), flujo=("Duplicada", None, None))
    sin_nada = await _rad(conn)

    assert await _ubic(conn, solo_destino) == ("Tecnología", 300)
    assert await _ubic(conn, solo_origen) == ("Origen", 100)
    assert await _ubic(conn, movido) == ("Control Interno", 500)
    assert await _ubic(conn, sin_codigo) == ("Duplicada", None)
    assert await _ubic(conn, sin_nada) == (None, None)
    # La dependencia de RADICACIÓN no se mueve con el flujo.
    assert (await conn.fetchrow("SELECT dest_dept, dest_dept_code FROM radicados WHERE id = $1", movido))[
        "dest_dept_code"
    ] == 300


async def test_nadie_puede_escribir_la_columna_generada(tenant_ctx) -> None:
    _, conn = await _en_tenant(tenant_ctx)
    rid = await _rad(conn, dest=("Tecnología", 300))
    for sql in (
        "UPDATE radicados SET ubicacion_dept = 'X' WHERE id = $1",
        "UPDATE radicados SET ubicacion_dept_code = 1 WHERE id = $1",
    ):
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(sql, rid)


async def test_la_migracion_037_es_idempotente(tenant_ctx) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    rid = await _rad(conn, dest=("Tecnología", 300))
    sql = next(p for p in runner._sql_files(runner.TENANT_DIR) if "037_radicado_ubicacion" in p.name)

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{tenant_schema(slug)}", public')
        await conn.execute(sql.read_text())

    assert await _ubic(conn, rid) == ("Tecnología", 300)
    assert await conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = $1 AND indexname = 'ix_radicados_ubicacion_dept_code'",
        tenant_schema(slug),
    ) == 1


async def test_el_filtro_por_ubicacion_va_en_el_where_del_select_y_del_count(tenant_ctx) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    repo = DocumentRepository(conn, slug)
    u = uuid.uuid4()
    # Hoy está en 500: movido por el flujo, o radicado allí sin movimientos.
    movido = await _rad(conn, dest=("Tecnología", 300), flujo=("Control Interno", 500, u))
    radicado_ahi = await _rad(conn, dest=("Control Interno", 500))
    reservado = await _rad(conn, dest=("Control Interno", 500), nivel=CLASIFICADA)
    # Iba dirigido a 500 pero el flujo ya lo sacó de ahí: NO está en 500.
    salio = await _rad(conn, dest=("Control Interno", 500), flujo=("Archivo", 200, None))

    docs, total = await repo.find_all(
        doc_type=None, status=None, metadata_filter=None, page=1, size=50,
        max_clearance=PUBLICA, ubicacion_dept_code=500,
    )
    assert {d.id for d in docs} == {movido, radicado_ahi}
    # El reservado no está en las filas NI en el total (no es un oráculo de recuento).
    assert total == 2

    docs, total = await repo.find_all(
        doc_type=None, status=None, metadata_filter=None, page=1, size=50,
        max_clearance=CLASIFICADA, ubicacion_dept_code=500,
    )
    assert {d.id for d in docs} == {movido, radicado_ahi, reservado} and total == 3
    assert salio not in {d.id for d in docs}

    # Paginar no cambia el total, que sigue siendo el del filtro.
    docs, total = await repo.find_all(
        doc_type=None, status=None, metadata_filter=None, page=1, size=1,
        max_clearance=CLASIFICADA, ubicacion_dept_code=500,
    )
    assert len(docs) == 1 and total == 3


async def test_el_poseedor_solo_sale_en_el_detalle(tenant_ctx) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    repo = DocumentRepository(conn, slug)
    u = uuid.uuid4()
    rid = await _rad(conn, dest=("Tecnología", 300), flujo=("Control Interno", 500, u))

    detalle = await repo.get_by_id(rid, PUBLICA)
    assert detalle is not None
    assert (detalle.flujo_usuario, detalle.ubicacion_dept, detalle.ubicacion_dept_code) == (u, "Control Interno", 500)
    por_numero = await repo.get_by_tracking_number(detalle.tracking_number, PUBLICA)
    assert por_numero is not None and por_numero.flujo_usuario == u

    docs, _ = await repo.find_all(
        doc_type=None, status=None, metadata_filter=None, page=1, size=10, max_clearance=PUBLICA
    )
    assert docs[0].flujo_usuario is None  # el listado no lo selecciona
    assert docs[0].ubicacion_dept == "Control Interno"


async def test_el_reporte_de_ubicacion_excluye_terminales_y_el_de_radicacion_no(tenant_ctx) -> None:
    _, conn = await _en_tenant(tenant_ctx)
    await _rad(conn, dest=("Tecnología", 300), flujo=("Control Interno", 500, None))
    await _rad(conn, dest=("Tecnología", 300), flujo=("Control Interno", 500, None), status="closed")
    await _rad(conn, dest=("Tecnología", 300), status="anulado")

    *_, por_radicacion, por_ubicacion = await ReportRepository(conn).radicado_summary(None, None)

    assert {r["dep"]: r["n"] for r in por_radicacion} == {"Tecnología": 3}
    assert {r["dep"]: r["n"] for r in por_ubicacion} == {"Control Interno": 1}

    # Con filtro de fechas, el de terminales se combina (no se pisa).
    *_, por_ubicacion = await ReportRepository(conn).radicado_summary(date(2000, 1, 1), date(2100, 1, 1))
    assert {r["dep"]: r["n"] for r in por_ubicacion} == {"Control Interno": 1}


async def test_el_export_lleva_la_ubicacion_como_dato_informativo(tenant_ctx) -> None:
    _, conn = await _en_tenant(tenant_ctx)
    await _rad(conn, dest=("Tecnología", 300), flujo=("Control Interno", 500, uuid.uuid4()))

    (fila,) = await ExportRepository(conn).list_radicados(max_clearance=PUBLICA, year=None, doc_type=None)

    assert (fila["ubicacion_dept"], fila["ubicacion_dept_code"]) == ("Control Interno", 500)
    assert "flujo_usuario" not in fila  # el poseedor no sale del sistema en un paquete


async def test_el_patch_de_dest_dept_mueve_la_ubicacion_solo_si_el_flujo_no_la_movio(tenant_ctx) -> None:
    from app.schemas.document import DocumentUpdate

    slug, conn = await _en_tenant(tenant_ctx)
    repo = DocumentRepository(conn, slug)
    await _dependencias(conn)
    sin_pasos = await _rad(conn, dest=("Tecnología", 300))
    con_flujo = await _rad(conn, dest=("Tecnología", 300), flujo=("Control Interno", 500, None))

    r1 = await repo.update(sin_pasos, DocumentUpdate(dest_dept="Archivo"), PUBLICA)
    r2 = await repo.update(con_flujo, DocumentUpdate(dest_dept="Archivo"), PUBLICA)

    # Sin pasos la ubicación ES la radicación: el RETURNING ya trae el nombre nuevo.
    assert r1 is not None and r1.ubicacion_dept == "Archivo"
    # Con el flujo moviéndolo, corregir el destino de radicación no lo desplaza.
    assert r2 is not None and r2.ubicacion_dept == "Control Interno"
    assert r2.ubicacion_dept_code == 500 and r2.dest_dept == "Archivo"


async def _dependencias(conn: asyncpg.Connection) -> None:
    # La tabla la crea tenant-service; el fixture solo aplica las de document-service.
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS dependencias (codigo int PRIMARY KEY, nombre text NOT NULL, "
        "activo boolean NOT NULL DEFAULT true)"
    )
    await conn.execute(
        "INSERT INTO dependencias (codigo, nombre, activo) VALUES "
        "(400, 'Archivo', true), (401, 'Duplicada', true), (402, 'Duplicada', true), (403, 'Inactiva', false)"
    )


async def test_el_patch_de_dest_dept_no_deja_el_codigo_de_otra_dependencia(tenant_ctx) -> None:
    from app.schemas.document import DocumentUpdate

    slug, conn = await _en_tenant(tenant_ctx)
    await _dependencias(conn)
    repo = DocumentRepository(conn, slug)
    rid = await _rad(conn, dest=("Tecnología", 300))
    r = await repo.update(rid, DocumentUpdate(dest_dept="Archivo"), PUBLICA)
    assert r is not None and (r.ubicacion_dept, r.ubicacion_dept_code) == ("Archivo", 400)
    # Nombre ambiguo, inactivo o inexistente: el código NO se inventa ni se hereda.
    for nombre in ("Duplicada", "Inactiva", "Inexistente"):
        r = await repo.update(rid, DocumentUpdate(dest_dept=nombre), PUBLICA)
        assert r is not None and r.ubicacion_dept == nombre and r.ubicacion_dept_code is None, nombre


async def test_el_patch_de_dest_dept_en_un_radicado_cerrado_se_rechaza(tenant_ctx) -> None:
    from app.schemas.document import DocumentUpdate
    from app.services.document_service import DestDeptImmutableError

    slug, conn = await _en_tenant(tenant_ctx)
    await _dependencias(conn)
    rid = await _rad(conn, dest=("Tecnología", 300), status="closed")
    from app.services.document_service import DocumentService
    svc = DocumentService(conn, slug)
    with pytest.raises(DestDeptImmutableError):
        await svc.update(rid, DocumentUpdate(dest_dept="Archivo"), PUBLICA, uuid.uuid4())
    assert (await _ubic(conn, rid)) == ("Tecnología", 300)
