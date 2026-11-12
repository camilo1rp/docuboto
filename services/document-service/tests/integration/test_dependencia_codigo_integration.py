# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Resolución de la dependencia por código contra Postgres REAL (B5, ADR-032).

Cubre lo que un `conn` simulado no puede decir:

* la tabla de evidencia `dependencia_codigo_backfill_doc` es de solo inserción y
  su `resultado` es de un conjunto cerrado;
* un borrador con dos homónimas activas NO radica hacia la de menor código;
* renombrar una dependencia no cambia a quién se radica ni la tenencia;
* PATCH del destino: el par (nombre, código) lo escribe el catálogo.

Mutación verificada: restaurar `ORDER BY codigo LIMIT 1` en
`_datos_minimos_del_borrador` -> cae
`test_homonimas_el_borrador_sin_codigo_no_radica_hacia_ninguna`.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from document.core.database import tenant_schema
from document.schemas.borrador import BorradorCreate, BorradorUpdate
from document.schemas.document import DocumentUpdate
from document.services.borrador_service import BorradorService
from document.services.document_service import DocumentService

pytestmark = pytest.mark.asyncio

AUTOR = uuid.uuid4()


async def _prep(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, conn


async def _dep(conn, codigo: int, nombre: str, activo: bool = True) -> None:
    await conn.execute(
        "INSERT INTO dependencias (codigo, sigla, nombre, activo) VALUES ($1, $2, $3, $4)",
        codigo,
        f"D{codigo}",
        nombre,
        activo,
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
    """Borrador que solo trae el nombre del destino, sin código."""
    return await conn.fetchval(
        "INSERT INTO borrador (doc_type, subject, dest_dept, creado_por) "
        "VALUES ('I', 'Interno', $1, $2) RETURNING id",
        dest,
        AUTOR,
    )


# --- evidencia ---------------------------------------------------------------


_EVIDENCIA = (
    "INSERT INTO dependencia_codigo_backfill_doc "
    "(tabla, fila_id, columna, nombre, codigo, candidatos, resultado) "
    "VALUES ('radicados', gen_random_uuid(), 'dest_dept', 'X', $1, $2, $3)"
)


async def test_la_evidencia_es_de_solo_insercion(tenant_ctx) -> None:
    _, conn = await _prep(tenant_ctx)
    await conn.execute(_EVIDENCIA, None, 0, "huerfano")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("UPDATE dependencia_codigo_backfill_doc SET nombre = 'x'")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("DELETE FROM dependencia_codigo_backfill_doc")


async def test_mapeado_no_es_un_valor_de_resultado(tenant_ctx) -> None:
    """«Mapeado» se deriva de `historico_id`; no es un valor de `resultado`."""
    _, conn = await _prep(tenant_ctx)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(_EVIDENCIA, 1, 1, "mapeado")


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


# --- reporte ------------------------------------------------------------------


async def test_el_reporte_rotula_sin_codigo_con_etiqueta_fija_y_no_con_un_nombre_arbitrario(tenant_ctx) -> None:
    """Mutación: volver a `MIN(dest_dept)` rotula el grupo sin código con un nombre cualquiera."""
    from document.repositories.report_repository import ReportRepository

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
