# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Migración 046 y resolución por código contra Postgres REAL (B5, ADR-032).

Cubre lo que un `conn` simulado no puede decir:

* el backfill escribe SOLO con un único candidato (ambiguo/huérfano quedan NULL con
  evidencia) y es idempotente;
* la evidencia es de solo inserción y el runner la ancla en `audit_log`;
* sin la función de tenant-service y con filas pendientes la migración FALLA (no se
  marca aplicada);
* un borrador con dos homónimas activas NO radica hacia la de menor código;
* renombrar una dependencia no cambia a quién se radica ni la tenencia;
* PATCH del destino: el par (nombre, código) lo escribe el catálogo.

Mutaciones verificadas: quitar `count(*) = 1` de la función (copia del conftest) hace
que el ambiguo se asigne -> cae `test_el_ambiguo_queda_sin_codigo_con_evidencia`;
restaurar `ORDER BY codigo LIMIT 1` en `_datos_minimos_del_borrador` -> cae
`test_homonimas_el_borrador_sin_codigo_no_radica_hacia_ninguna`.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.schemas.borrador import BorradorCreate, BorradorUpdate
from app.schemas.document import DocumentUpdate
from app.services.borrador_service import BorradorService
from app.services.document_service import DocumentService
from migrations import runner

pytestmark = pytest.mark.asyncio

_046 = "046_dependencia_por_codigo.sql"
AUTOR = uuid.uuid4()


def _sql_046() -> str:
    (ruta,) = [p for p in runner._sql_files(runner.TENANT_DIR) if p.name == _046]
    return ruta.read_text()


async def _prep(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, conn


async def _dep(conn, codigo: int, nombre: str, activo: bool = True) -> None:
    await conn.execute(
        "INSERT INTO dependencias (codigo, nombre, activo) VALUES ($1, $2, $3)", codigo, nombre, activo
    )


async def _radicado(conn, *, dest: str | None = None, origin: str | None = None) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code, "
        "dest_dept, origin_dept) VALUES ($1, 'E', 2026, 1, 'ASUNTO', 'DEMO', $2, $3) RETURNING id",
        f"2026-DEMO-E-{uuid.uuid4().hex[:6]}",
        dest,
        origin,
    )


async def _borrador_viejo(conn, dest: str | None) -> uuid.UUID:
    """Borrador como los anteriores a la 046: solo el nombre del destino."""
    return await conn.fetchval(
        "INSERT INTO borrador (doc_type, subject, dest_dept, creado_por) "
        "VALUES ('I', 'Interno', $1, $2) RETURNING id",
        dest,
        AUTOR,
    )


# --- backfill ---------------------------------------------------------------


async def test_el_unico_candidato_se_escribe_y_no_se_toca_updated_at(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    rid = await _radicado(conn, dest="Archivo", origin="Archivo")
    bid = await _borrador_viejo(conn, "Archivo")
    antes = await conn.fetchval("SELECT updated_at FROM radicados WHERE id = $1", rid)

    await conn.execute(_sql_046())

    r = await conn.fetchrow(
        "SELECT dest_dept_code, origin_dept_code, updated_at, ubicacion_dept_code "
        "FROM radicados WHERE id = $1",
        rid,
    )
    assert (r["dest_dept_code"], r["origin_dept_code"]) == (901, 901)
    assert r["ubicacion_dept_code"] == 901  # la generada gana la ubicación por código
    assert r["updated_at"] == antes
    assert await conn.fetchval("SELECT dest_dept_code FROM borrador WHERE id = $1", bid) == 901


async def test_el_ambiguo_queda_sin_codigo_con_evidencia(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo")
    rid = await _radicado(conn, dest="Archivo")
    await conn.execute(_sql_046())
    assert await conn.fetchval("SELECT dest_dept_code FROM radicados WHERE id = $1", rid) is None
    e = await conn.fetchrow(
        "SELECT resultado, candidatos, codigo FROM dependencia_codigo_backfill_doc WHERE fila_id = $1", rid
    )
    assert (e["resultado"], e["candidatos"], e["codigo"]) == ("ambiguo", 2, None)


async def test_el_huerfano_queda_sin_codigo_con_evidencia(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    rid = await _radicado(conn, dest="Ya no existe")
    await conn.execute(_sql_046())
    assert await conn.fetchval("SELECT dest_dept_code FROM radicados WHERE id = $1", rid) is None
    assert await conn.fetchval(
        "SELECT resultado FROM dependencia_codigo_backfill_doc WHERE fila_id = $1", rid
    ) == "huerfano"


async def test_un_nombre_anterior_resuelve_por_el_historial_y_un_mapeo_manual_tambien(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Gestión Documental")
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES "
        "(901, 'Archivo', 'renombre'), (901, 'Archivo central', 'mapeo_manual')"
    )
    a = await _radicado(conn, dest="Archivo")
    b = await _radicado(conn, dest="Archivo central")
    await conn.execute(_sql_046())
    for rid in (a, b):
        assert await conn.fetchval("SELECT dest_dept_code FROM radicados WHERE id = $1", rid) == 901


async def test_un_radicado_con_codigo_ya_puesto_no_se_toca(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Otra")
    rid = await _radicado(conn, dest="Archivo")
    await conn.execute("UPDATE radicados SET dest_dept_code = 902 WHERE id = $1", rid)
    await conn.execute(_sql_046())
    assert await conn.fetchval("SELECT dest_dept_code FROM radicados WHERE id = $1", rid) == 902


async def test_la_046_es_idempotente(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _radicado(conn, dest="Archivo")
    await _radicado(conn, dest="Nadie")
    await conn.execute(_sql_046())
    n = await conn.fetchval("SELECT count(*) FROM dependencia_codigo_backfill_doc")
    assert n == 2
    await conn.execute(_sql_046())
    assert await conn.fetchval("SELECT count(*) FROM dependencia_codigo_backfill_doc") == n


async def test_la_evidencia_es_de_solo_insercion(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _radicado(conn, dest="Nadie")
    await conn.execute(_sql_046())
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("UPDATE dependencia_codigo_backfill_doc SET nombre = 'x'")
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("DELETE FROM dependencia_codigo_backfill_doc")


async def test_sin_la_funcion_y_con_filas_pendientes_la_046_falla_cerrada(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _radicado(conn, dest="Archivo")
    await conn.execute("DROP FUNCTION resolver_codigo_dependencia(text, boolean)")
    with pytest.raises(asyncpg.RaiseError, match="resolver_codigo_dependencia"):
        await conn.execute(_sql_046())


async def test_sin_la_funcion_y_sin_filas_pendientes_no_hay_nada_que_perder(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await conn.execute("DROP FUNCTION resolver_codigo_dependencia(text, boolean)")
    await conn.execute(_sql_046())  # no lanza


async def test_el_ancla_firma_el_contenido_ordenado_y_no_se_repite(tenant_ctx) -> None:
    slug, conn = await _prep(tenant_ctx)
    schema = tenant_schema(slug)
    await _dep(conn, 901, "Archivo")
    await _radicado(conn, dest="Archivo")
    await _radicado(conn, dest="Nadie")
    await conn.execute(_sql_046())

    assert await runner.anclar_evidencia(conn, schema, _046, tenant_slug=slug) is True
    a = await conn.fetchrow(
        "SELECT payload FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "document.dependencia_codigo.backfill",
    )
    p = json.loads(a["payload"]) if isinstance(a["payload"], str) else a["payload"]
    filas = await conn.fetch(
        "SELECT * FROM dependencia_codigo_backfill_doc ORDER BY tabla, fila_id, columna, id"
    )
    columnas = [c for c in filas[0].keys() if c not in ("id", "at")]
    esperado = hashlib.sha256(
        "\n".join("|".join(str(f[c]) for c in columnas) for f in filas).encode()
    ).hexdigest()
    assert p["sha256"] == esperado and p["filas"] == 2
    assert await runner.anclar_evidencia(conn, schema, _046, tenant_slug=slug) is False


# --- borrador: nunca se elige en silencio -------------------------------------


async def test_homonimas_el_borrador_sin_codigo_no_radica_hacia_ninguna(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo")
    bid = await _borrador_viejo(conn, "Archivo")
    row = await conn.fetchrow("SELECT * FROM borrador WHERE id = $1", bid)
    svc = BorradorService(conn, "demo")
    with pytest.raises(HTTPException) as exc:
        await svc._datos_minimos_del_borrador(row, AUTOR)
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "dependencia_ambigua"


async def test_una_sola_candidata_se_deriva_y_el_codigo_del_borrador_gana(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    svc = BorradorService(conn, "demo")
    row = await conn.fetchrow("SELECT * FROM borrador WHERE id = $1", await _borrador_viejo(conn, "Archivo"))
    assert (await svc._datos_minimos_del_borrador(row, AUTOR))[2] == 901  # control positivo
    await _dep(conn, 902, "Archivo")  # ahora hay dos: el borrador CON código no se ve afectado
    await conn.execute("UPDATE borrador SET dest_dept_code = 901 WHERE id = $1", row["id"])
    row = await conn.fetchrow("SELECT * FROM borrador WHERE id = $1", row["id"])
    assert (await svc._datos_minimos_del_borrador(row, AUTOR))[2] == 901


async def test_renombrar_no_cambia_a_quien_se_radica(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    svc = BorradorService(conn, "demo")
    b = await svc.crear(
        BorradorCreate(doc_type="I", subject="Memorando", dest_dept_code=901), AUTOR, max_clearance=3
    )
    assert (b.dest_dept, b.dest_dept_code) == ("Archivo", 901)
    await conn.execute("UPDATE dependencias SET nombre = 'Gestión Documental' WHERE codigo = 901")
    row = await conn.fetchrow("SELECT * FROM borrador WHERE id = $1", b.id)
    assert (await svc._datos_minimos_del_borrador(row, AUTOR))[2] == 901


async def test_crear_y_editar_borrador_escriben_el_par_desde_el_catalogo(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Jurídica")
    await _dep(conn, 903, "Jurídica")
    svc = BorradorService(conn, "demo")
    # Alias único: el código sale del nombre.
    b = await svc.crear(
        BorradorCreate(doc_type="I", subject="Memorando", dest_dept="Archivo"), AUTOR, max_clearance=3
    )
    assert b.dest_dept_code == 901
    # Alias ambiguo: 422.
    with pytest.raises(HTTPException) as exc:
        await svc.crear(
            BorradorCreate(doc_type="I", subject="Memorando", dest_dept="Jurídica"), AUTOR, max_clearance=3
        )
    assert exc.value.detail["error"] == "dependencia_ambigua"
    # Código con nombre discordante: 422.
    with pytest.raises(HTTPException) as exc:
        await svc.crear(
            BorradorCreate(doc_type="I", subject="Memorando", dest_dept="Archivo", dest_dept_code=902),
            AUTOR,
            max_clearance=3,
        )
    assert exc.value.detail["error"] == "dependencia_inconsistente"
    # Editar: el código manda y reescribe el nombre; no queda el código de OTRA dependencia.
    e = await svc.editar(b.id, BorradorUpdate(dest_dept_code=902), max_clearance=3, actor=AUTOR)
    assert (e.dest_dept, e.dest_dept_code) == ("Jurídica", 902)
    # Un destino que no es dependencia (Salida a un tercero) se conserva como texto, sin código.
    e = await svc.editar(b.id, BorradorUpdate(dest_dept="Ciudadano X"), max_clearance=3, actor=AUTOR)
    assert (e.dest_dept, e.dest_dept_code) == ("Ciudadano X", None)


# --- PATCH del destino de un radicado -------------------------------------------


async def test_patch_destino_escribe_el_par_entero_y_la_ubicacion_sigue_al_codigo(tenant_ctx) -> None:
    slug, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Jurídica")
    await _dep(conn, 903, "Contratos")
    await _dep(conn, 904, "Contratos")
    await _dep(conn, 905, "Vieja", activo=False)
    rid = await _radicado(conn, dest="Archivo")
    await conn.execute("UPDATE radicados SET dest_dept_code = 901 WHERE id = $1", rid)
    svc = DocumentService(conn, slug)

    r = await svc.update(rid, DocumentUpdate(dest_dept_code=902), max_clearance=3, actor=AUTOR)
    assert (r.dest_dept, r.dest_dept_code) == ("Jurídica", 902)
    assert await conn.fetchval("SELECT ubicacion_dept_code FROM radicados WHERE id = $1", rid) == 902

    for payload, error in (
        (DocumentUpdate(dest_dept="Contratos"), "dependencia_ambigua"),
        (DocumentUpdate(dest_dept="Nadie"), "dependencia_desconocida"),
        (DocumentUpdate(dest_dept_code=905), "dependencia_desconocida"),  # inactiva
        (DocumentUpdate(dest_dept_code=901, dest_dept="Jurídica"), "dependencia_inconsistente"),
    ):
        with pytest.raises(HTTPException) as exc:
            await svc.update(rid, payload, max_clearance=3, actor=AUTOR)
        assert exc.value.detail["error"] == error
    # Nada de lo rechazado dejó rastro: sigue en 902.
    assert await conn.fetchval("SELECT dest_dept_code FROM radicados WHERE id = $1", rid) == 902

    # Alias único: se resuelve y se guarda el nombre del CATÁLOGO.
    r = await svc.update(rid, DocumentUpdate(dest_dept="Archivo"), max_clearance=3, actor=AUTOR)
    assert (r.dest_dept, r.dest_dept_code) == ("Archivo", 901)


# --- 047: la evidencia distingue lo decidido por un humano --------------------


def _sql_047() -> str:
    (ruta,) = [p for p in runner._sql_files(runner.TENANT_DIR) if p.name.startswith("047_")]
    return ruta.read_text()


_COLS_ANCLA_046 = "tabla, fila_id, columna, nombre, codigo, candidatos, resultado"


async def _huella_046(conn) -> str:
    """SHA-256 como lo calcula el runner para el ancla de la 046 (columnas fijadas en el payload)."""
    import hashlib

    filas = await conn.fetch(
        f"SELECT {_COLS_ANCLA_046} FROM dependencia_codigo_backfill_doc ORDER BY tabla, fila_id, columna, id"
    )
    cols = [c.strip() for c in _COLS_ANCLA_046.split(",")]
    contenido = "\n".join("|".join(str(f[c]) for c in cols) for f in filas)
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


async def test_homonimas_con_mapeo_se_resuelven_y_la_047_las_enlaza_sin_tocar_lo_anclado(tenant_ctx) -> None:
    """Mutaciones: el UNION plano del resolver deja la fila 'ambiguo'; que la 047 reclasifique
    `resultado` (-> 'mapeado') cambia la huella que ancla la 046 y esta prueba cae."""
    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo")
    rid = await _radicado(conn, dest="Archivo")
    hid = await conn.fetchval(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
        "VALUES (902, 'Archivo', 'mapeo_manual') RETURNING id"
    )
    await conn.execute(_sql_046())
    assert await conn.fetchval("SELECT dest_dept_code FROM radicados WHERE id = $1", rid) == 902
    antes = await _huella_046(conn)

    await conn.execute(_sql_047())
    await conn.execute(_sql_047())  # idempotente
    e = await conn.fetchrow("SELECT resultado, codigo, historico_id FROM dependencia_codigo_backfill_doc WHERE fila_id = $1", rid)
    # «Mapeado» se DERIVA de historico_id; `resultado` (anclado) sigue siendo 'resuelto'.
    assert (e["resultado"], e["codigo"], e["historico_id"]) == ("resuelto", 902, hid)
    assert await _huella_046(conn) == antes
    # La evidencia sigue siendo de solo inserción tras apartar el trigger dentro de la 047.
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("UPDATE dependencia_codigo_backfill_doc SET nombre = 'x'")


async def test_mapeado_no_es_un_valor_de_resultado(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await conn.execute(_sql_047())
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO dependencia_codigo_backfill_doc (tabla, fila_id, columna, nombre, codigo, candidatos, resultado) "
            "VALUES ('radicados', gen_random_uuid(), 'dest_dept', 'X', 1, 1, 'mapeado')"
        )


async def test_el_ancla_de_la_047_tiene_accion_propia_y_distinta_de_la_046() -> None:
    assert runner._ANCLAS["047_backfill_doc_mapeado.sql"][1] == "document.dependencia_codigo.backfill_047"
    assert (
        runner._ANCLAS["047_backfill_doc_mapeado.sql"][1]
        != runner._ANCLAS["046_dependencia_por_codigo.sql"][1]
    )


async def test_el_reporte_rotula_sin_codigo_con_etiqueta_fija_y_no_con_un_nombre_arbitrario(tenant_ctx) -> None:
    """Mutación: volver a `MIN(dest_dept)` rotula el grupo sin código con un nombre cualquiera."""
    from app.repositories.report_repository import ReportRepository

    _, conn = await _prep(tenant_ctx)
    await _dep(conn, 901, "Archivo")
    await _radicado(conn, dest="Archivo")
    await conn.execute("UPDATE radicados SET dest_dept_code = 901")
    for nombre in ("Zeta vieja", "Alfa vieja"):  # sin código, nombres distintos
        await _radicado(conn, dest=nombre)
    _, _, _, por_dependencia, _ = await ReportRepository(conn).radicado_summary(None, None)
    filas = {r["codigo"]: (r["dep"], r["n"]) for r in por_dependencia}
    assert filas[901] == ("Archivo", 1)
    assert filas[None] == ("(sin código)", 2)
