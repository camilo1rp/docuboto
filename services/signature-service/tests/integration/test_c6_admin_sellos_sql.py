# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 — la consulta de administración de sellos de archivo contra Postgres REAL.

Lo que un doble no puede decir: que el SQL clasifica bien (`vencido`/`por_vencer`/`sin_revocacion`/
`pendiente_worm`/`rechazado`), que solo cuenta el ÚLTIMO eslabón, que el orden es el declarado
(`vence_at ASC, firma_id`), que el total sale del MISMO filtro que las filas y que el acotado por
clearance se decide en la consulta. También los productores y el CHECK de la migración 027.

Mutaciones: tomar CUALQUIER eslabón y no el último (cae `test_solo_cuenta_el_ultimo_eslabon`); ordenar por
`created_at` (cae `test_orden_declarado`); calcular `vence_at` solo con la hoja (cae
`test_lo_primero_que_caduca_es_la_ca`); filtrar el total aparte de las filas (cae
`test_el_total_sale_del_mismo_filtro_que_las_filas`); decidir `objeto_reservado` en Python tras la consulta
(cae `test_el_objeto_reservado_se_acota_en_la_consulta`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import _schema as tenant_schema
from app.core.database import get_tenant_conn
from app.main import app
from app.repositories.firma_repository import FirmaRepository

pytestmark = pytest.mark.asyncio

AHORA = datetime.now(UTC)
ADMIN = uuid.UUID("11111111-1111-1111-1111-111111111111")


async def _firma(conn, *, objeto_tipo: str = "indice", objeto_id: str | None = None) -> uuid.UUID:  # noqa: ANN001
    firma_id = uuid.uuid4()
    await FirmaRepository(conn).insert({
        "id": firma_id, "objeto_tipo": objeto_tipo, "objeto_id": objeto_id or f"obj-{firma_id.hex[:6]}",
        "payload_sha256": "0" * 64, "firma_blob": "x", "provider": "xades_local",
        "sello_tiempo": AHORA, "tipo_firma": "sello_sistema",
    })
    return firma_id


async def _eslabon(  # noqa: ANN202
    conn, firma_id, ordinal: int, *, vence_en_dias: float, estado: str = "vigente",  # noqa: ANN001
    cadena_en_dias: float | None = None, fuente: str = "crl", aviso: str | None = None,
):
    r = await FirmaRepository(conn).insert_sello_archivo(
        firma_id=firma_id, ordinal=ordinal, minio_key=f"{uuid.uuid4()}/x-lta{ordinal}.xml",
        xml_sha256=uuid.uuid4().hex * 2, gen_time=AHORA - timedelta(days=100 - ordinal),
        tsa_cert_fingerprint="ab" * 32, tsa_cert_not_after=AHORA + timedelta(days=vence_en_dias),
        tsa_cadena_not_after=None if cadena_en_dias is None else AHORA + timedelta(days=cadena_en_dias),
        tsa_revocacion_fuente=fuente, revocacion_tsa_previa=None if ordinal == 0 else "crl",
        estado=estado,
    )
    if aviso:
        await conn.execute("UPDATE firma_sello_archivo SET ultimo_aviso = $2 WHERE id = $1", r["id"], aviso)
    return r


async def _sembrar(conn, slug) -> dict[str, uuid.UUID]:  # noqa: ANN001
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    f = {k: await _firma(conn) for k in "ABCDEFGHI"}
    await _eslabon(conn, f["A"], 0, vence_en_dias=30)                                    # por_vencer
    await _eslabon(conn, f["B"], 0, vence_en_dias=200)                                   # sana
    await _eslabon(conn, f["C"], 0, vence_en_dias=-5, estado="caducado")                 # vencido
    await _eslabon(conn, f["D"], 0, vence_en_dias=60, fuente="ninguna")                  # por_vencer + sin_revocacion
    await _eslabon(conn, f["E"], 0, vence_en_dias=400, estado="pendiente_worm")          # pendiente_worm
    await _eslabon(conn, f["F"], 0, vence_en_dias=300, fuente="ocsp",
                   aviso="rechazada:xml_no_coincide_con_el_registro")                    # rechazado
    await _eslabon(conn, f["G"], 0, vence_en_dias=300, cadena_en_dias=20)                # por_vencer por la CA
    await _eslabon(conn, f["H"], 0, vence_en_dias=-50, estado="caducado")                # el 0 ya no importa...
    await _eslabon(conn, f["H"], 1, vence_en_dias=300)                                   # ...manda el último: sana
    await _eslabon(conn, f["I"], 0, vence_en_dias=250, fuente="autofirmada")             # sin_revocacion
    return f


async def _lista(conn, **kw):  # noqa: ANN001, ANN003, ANN202
    base = {"situaciones": None, "dias": 90, "clearance": 5, "limit": 100, "offset": 0}
    return await FirmaRepository(conn).listar_sellos_archivo(**{**base, **kw})


async def test_clasifica_cada_situacion(tenant_ctx) -> None:  # noqa: ANN001
    slug, conn = tenant_ctx
    f = await _sembrar(conn, slug)
    filas, total = await _lista(conn)
    por_firma = {r["firma_id"]: set(r["situaciones"]) for r in filas}
    assert por_firma == {
        f["A"]: {"por_vencer"},
        f["C"]: {"vencido"},
        f["D"]: {"por_vencer", "sin_revocacion"},
        f["E"]: {"pendiente_worm"},
        f["F"]: {"rechazado"},
        f["G"]: {"por_vencer"},
        f["I"]: {"sin_revocacion"},
    }, "B (sana) y H (el último eslabón sano) no son anomalías"
    assert total == 7


async def test_solo_cuenta_el_ultimo_eslabon(tenant_ctx) -> None:  # noqa: ANN001
    slug, conn = tenant_ctx
    f = await _sembrar(conn, slug)
    filas, _ = await _lista(conn)
    assert f["H"] not in {r["firma_id"] for r in filas}


async def test_lo_primero_que_caduca_es_la_ca(tenant_ctx) -> None:  # noqa: ANN001
    """G: la hoja vence en 300 d pero su CA en 20: `vence_at` es la CA. Mutación: solo la hoja -> cae."""
    slug, conn = tenant_ctx
    f = await _sembrar(conn, slug)
    filas, _ = await _lista(conn, situaciones=["por_vencer"])
    g = next(r for r in filas if r["firma_id"] == f["G"])
    assert 19 <= g["dias_restantes"] <= 20
    assert g["vence_at"] < AHORA + timedelta(days=25)


async def test_orden_declarado(tenant_ctx) -> None:  # noqa: ANN001
    slug, conn = tenant_ctx
    await _sembrar(conn, slug)
    filas, _ = await _lista(conn)
    vence = [r["vence_at"] for r in filas]
    assert vence == sorted(vence), "vence_at ASC: lo más urgente primero"
    assert filas[0]["estado"] == "caducado", "el ya vencido antecede a lo que está por vencer"


async def test_el_total_sale_del_mismo_filtro_que_las_filas(tenant_ctx) -> None:  # noqa: ANN001
    """Para cada filtro y cada página: el total NO cambia con la página y es el de las filas sin paginar."""
    slug, conn = tenant_ctx
    await _sembrar(conn, slug)
    for sit in (None, ["por_vencer"], ["sin_revocacion"], ["vencido", "pendiente_worm"], ["rechazado"]):
        todas, total = await _lista(conn, situaciones=sit)
        assert total == len(todas) > 0
        pagina1, t1 = await _lista(conn, situaciones=sit, limit=2, offset=0)
        pagina2, t2 = await _lista(conn, situaciones=sit, limit=2, offset=2)
        assert t1 == t2 == total
        assert [r["firma_id"] for r in pagina1 + pagina2] == [r["firma_id"] for r in todas][: len(pagina1 + pagina2)]
    # una página más allá del final: sin filas, y el total sigue siendo el real
    vacia, tv = await _lista(conn, limit=10, offset=1000)
    assert vacia == [] and tv == 7


async def test_el_margen_cambia_lo_que_esta_por_vencer(tenant_ctx) -> None:  # noqa: ANN001
    slug, conn = tenant_ctx
    f = await _sembrar(conn, slug)
    estrecho, _ = await _lista(conn, situaciones=["por_vencer"], dias=25)
    assert {r["firma_id"] for r in estrecho} == {f["G"]}
    ancho, _ = await _lista(conn, situaciones=["por_vencer"], dias=500)
    assert {f["A"], f["B"], f["D"], f["G"], f["F"], f["I"], f["H"]} <= {r["firma_id"] for r in ancho}


async def test_resumen_cuenta_por_situacion(tenant_ctx) -> None:  # noqa: ANN001
    slug, conn = tenant_ctx
    await _sembrar(conn, slug)
    assert await FirmaRepository(conn).resumen_sellos_archivo(dias=90) == {
        "por_vencer": 3, "vencido": 1, "sin_revocacion": 2, "pendiente_worm": 1, "rechazado": 1,
    }


async def test_el_aviso_de_sin_revocacion_o_tsa_revocada_tambien_cuenta(tenant_ctx) -> None:  # noqa: ANN001
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    a, b = await _firma(conn), await _firma(conn)
    await _eslabon(conn, a, 0, vence_en_dias=250, fuente="crl", aviso="sin_revocacion_tsa")
    await _eslabon(conn, b, 0, vence_en_dias=250, fuente="ocsp", aviso="tsa_revocada")
    filas, _ = await _lista(conn, situaciones=["sin_revocacion"])
    assert {r["firma_id"] for r in filas} == {a, b}


# --- acotado por clearance --------------------------------------------------------------------


async def _con_radicados(conn, slug) -> tuple[uuid.UUID, uuid.UUID]:  # noqa: ANN001
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        "CREATE TABLE radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tracking_number text, "
        "nivel_seguridad int NOT NULL DEFAULT 1)"
    )
    await conn.execute("INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ('2026-X-E-000001', 3)")
    reservado = await _firma(conn, objeto_tipo="radicado", objeto_id="2026-X-E-000001")
    publico = await _firma(conn, objeto_tipo="indice", objeto_id="IDX")
    await _eslabon(conn, reservado, 0, vence_en_dias=10, fuente="ninguna")
    await _eslabon(conn, publico, 0, vence_en_dias=10, fuente="ninguna")
    return reservado, publico


async def test_el_objeto_reservado_se_acota_en_la_consulta(tenant_ctx) -> None:  # noqa: ANN001
    """La FILA se ve siempre (un sello reservado que va a caducar es una pérdida de prueba que el
    administrador tiene que ver); lo que se acota es el CAMPO. Y sin `expedientes` en el schema no hay error."""
    slug, conn = tenant_ctx
    reservado, publico = await _con_radicados(conn, slug)

    bajo, total = await _lista(conn, clearance=1)
    por = {r["firma_id"]: r for r in bajo}
    assert total == 2 and set(por) == {reservado, publico}
    assert por[reservado]["objeto_reservado"] is True
    assert por[reservado]["objeto_tipo"] is None and por[reservado]["objeto_id"] is None
    assert por[publico]["objeto_reservado"] is False and por[publico]["objeto_tipo"] == "indice"

    alto, _ = await _lista(conn, clearance=3)
    ver = {r["firma_id"]: r for r in alto}
    assert ver[reservado]["objeto_reservado"] is False and ver[reservado]["objeto_id"] == "2026-X-E-000001"


# --- por HTTP, con el SQL real ----------------------------------------------------------------


@pytest_asyncio.fixture
async def api(tenant_ctx):  # noqa: ANN001, ANN201
    slug, conn = tenant_ctx

    async def _dep():  # noqa: ANN202
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, slug, conn
    app.dependency_overrides.clear()


async def test_http_total_filas_y_asiento(api) -> None:  # noqa: ANN001
    ac, slug, conn = api
    await _sembrar(conn, slug)
    h = {"X-Tenant-Slug": slug, "X-User-Id": str(ADMIN)}
    with (
        patch("app.core.clearance.check_permission", AsyncMock(return_value=True)),
        patch("app.routers.admin_sellos.resolve_caller_clearance", AsyncMock(return_value=5)),
    ):
        r = await ac.get("/api/v1/signature/admin/sellos-archivo?situacion=por_vencer&size=2&page=2", headers=h)
        assert r.status_code == 200, r.text
        assert r.headers["X-Total-Count"] == "3" and len(r.json()) == 1
        resumen = await ac.get("/api/v1/signature/admin/sellos-archivo/resumen", headers=h)
        assert resumen.json()["por_vencer"] == 3 and resumen.json()["margen_dias"] == 90
    asientos = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = 'firma.sellos_archivo_consultados'",
        slug,
    )
    assert asientos == 2, "un asiento por petición"


async def test_http_sin_permiso_no_ve_nada(api) -> None:  # noqa: ANN001
    ac, slug, conn = api
    await _sembrar(conn, slug)
    h = {"X-Tenant-Slug": slug, "X-User-Id": str(ADMIN)}
    with patch("app.core.clearance.check_permission", AsyncMock(return_value=False)):
        r = await ac.get("/api/v1/signature/admin/sellos-archivo", headers=h)
    assert r.status_code == 403


# --- la migración 027 -------------------------------------------------------------------------


async def test_el_check_exige_la_revocacion_previa_solo_en_los_renovados(tenant_ctx) -> None:  # noqa: ANN001
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    f = await _firma(conn)
    base = (f, "k", "h" * 64, AHORA, "ab" * 32, AHORA + timedelta(days=9), None, "crl")
    sql = (
        "INSERT INTO firma_sello_archivo (firma_id, minio_key, xml_sha256, gen_time, tsa_cert_fingerprint, "
        "tsa_cert_not_after, tsa_cadena_not_after, tsa_revocacion_fuente, ordinal, revocacion_tsa_previa) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(sql, *base, 0, "crl")  # el original no tiene «previa»
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(sql, *base, 1, None)  # un renovado la exige
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(sql, *base[:7], "inventada", 0, None)
    await conn.execute(sql, *base, 0, None)
    await conn.execute(sql, *base, 1, "ocsp")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(sql, *base, 2, "ninguna")  # `ninguna` no es una revocación que respaldara nada


async def test_la_migracion_027_es_idempotente(tenant_ctx) -> None:  # noqa: ANN001
    from migrations import runner

    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    sql = next(p for p in runner._sql_files(runner.TENANT_DIR) if p.name.startswith("027_")).read_text()
    await conn.execute(sql)
    await conn.execute(sql)
