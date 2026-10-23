# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-4 (ADR-040) contra Postgres REAL: fases como fuente única (069), dependencia
productora (070), cuadro de clasificación derivado, `publicable` del tipo (071).

Lo que un mock no puede decir: que el CHECK de fases y el tope de dos niveles se disparan en
el motor, que los triggers de la 070 impiden tocar la productora fuera de `borrador`, que un
`aprobar` sin productora REVIERTE el cambio de estado, y que la CTE del organigrama encuentra
un ciclo en lugar de omitir sus nodos.

Mutaciones (todas verificadas, ver informe de la tanda):
- quitar `CYCLE id SET es_ciclo USING ruta` de `organigrama_hacia_arriba` →
  `test_un_ciclo_inyectado_es_409_no_un_cuadro_incompleto` cae;
- sacar `_exigir_productora` de la transacción de `aprobar` (antes del UPDATE) →
  `test_aprobar_sin_productora_revierte_el_estado` cae;
- quitar el `IF parent_parent IS NOT NULL` de la 069 → `test_el_motor_rechaza_tres_niveles` cae;
- quitar el `FOR SHARE`/chequeo de estado de la 070 → `test_la_productora_es_inmutable_fuera_de_borrador` cae.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.schemas.archive import (
    TipoDocumentalCreate,
    TipoDocumentalUpdate,
    TrdSerieAprobar,
    TrdSerieConvalidar,
    TrdSerieCreate,
    TrdSerieVersionCreate,
)
from app.services.archive_service import ArchiveService
from app.services.tipo_documental_service import TipoDocumentalService

pytestmark = pytest.mark.integration

_ACTA = TrdSerieAprobar(acta_comite="Acta itest", fecha_aprobacion_comite=date(2026, 1, 1))
_CONV = TrdSerieConvalidar(
    acto_administrativo="Resolución itest",
    fecha_convalidacion=date(2026, 3, 1),
    instancia_convalidante="Consejo itest",
)


async def _en(conn: asyncpg.Connection, slug: str) -> None:
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')


async def _serie(archive: ArchiveService, code: str, productoras: list[int] | None = None, **kw):
    return await archive.create_trd_serie(
        TrdSerieCreate(
            code=code,
            name=f"itest {code}",
            disposition="CT",
            archivo_gestion_years=2,
            archivo_central_years=8,
            dependencias_productoras=productoras,
            **kw,
        ),
        actor=None,
    )


# ── 069: fases como fuente única ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_las_columnas_legadas_ya_no_existen_y_las_vistas_siguen_vivas(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    cols = {
        r["column_name"]
        for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'trd_series'"
        )
    }
    assert not {"retention_years", "total_retention"} & cols
    assert {"archivo_gestion_years", "archivo_central_years"} <= cols
    # las vistas se recrearon (el DROP COLUMN las habría roto) y siguen discriminando
    await conn.fetchval("SELECT count(*) FROM v_trd_series")
    await conn.fetchval("SELECT count(*) FROM v_tvd_agrupaciones")


@pytest.mark.asyncio
async def test_el_trigger_de_inmutabilidad_sobrevive_al_drop_column(tenant_con_organigrama) -> None:
    """`reject_trd_version_mutation` se reescribió SIN las dos líneas legadas: si siguiera
    nombrándolas, el primer UPDATE reventaría con «record has no field retention_years»."""
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    s = await _serie(archive, f"TRG{uuid.uuid4().hex[:6]}", [110])
    await _en(conn, slug)
    await conn.execute("UPDATE trd_series SET name = 'editado' WHERE id = $1", s.id)  # borrador: pasa
    await conn.execute("UPDATE trd_series SET estado = 'migrada' WHERE id = $1", s.id)
    with pytest.raises(asyncpg.PostgresError, match="contenido inmutable"):
        await conn.execute("UPDATE trd_series SET archivo_central_years = 99 WHERE id = $1", s.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "columnas, valores",
    [
        ("code, name", "'A', 'a'"),  # TRD sin ninguna fase
        ("code, name, archivo_gestion_years", "'B', 'b', 2"),  # sin central
        ("code, name, archivo_central_years", "'C', 'c', 8"),  # sin gestión
        ("code, name, tipo_instrumento, fondo_nombre, fecha_extrema_inicial, fecha_extrema_final, "
         "justificacion_valoracion",
         "'D', 'd', 'TVD', 'F', '1980-01-01', '1990-01-01', 'j'"),  # TVD sin central
    ],
)
async def test_el_check_exige_las_fases(tenant_con_organigrama, columnas: str, valores: str) -> None:
    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    with pytest.raises(asyncpg.CheckViolationError, match="trd_series_fases_check"):
        await conn.execute(f"INSERT INTO trd_series ({columnas}) VALUES ({valores})")


@pytest.mark.asyncio
async def test_una_tvd_con_fase_central_y_sin_gestion_es_valida(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    await conn.execute(
        "INSERT INTO trd_series (code, name, tipo_instrumento, fondo_nombre, fecha_extrema_inicial, "
        "fecha_extrema_final, justificacion_valoracion, archivo_central_years) "
        "VALUES ('TV', 'tv', 'TVD', 'F', '1980-01-01', '1990-01-01', 'j', 10)"
    )  # control positivo del parametrizado anterior


@pytest.mark.asyncio
async def test_el_motor_rechaza_tres_niveles(tenant_con_organigrama) -> None:
    """serie > subserie > sub-subserie: el trigger de la 069 lo impide aunque el servicio
    se salte (defensa en profundidad del tope de dos niveles)."""
    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    serie = await conn.fetchval(
        "INSERT INTO trd_series (code, name, archivo_gestion_years, archivo_central_years) "
        "VALUES ('S', 's', 1, 1) RETURNING id"
    )
    sub = await conn.fetchval(
        "INSERT INTO trd_series (code, name, parent_id, archivo_gestion_years, archivo_central_years) "
        "VALUES ('S-1', 'sub', $1, 1, 1) RETURNING id",
        serie,
    )
    with pytest.raises(asyncpg.CheckViolationError, match="trd_jerarquia_dos_niveles"):
        await conn.execute(
            "INSERT INTO trd_series (code, name, parent_id, archivo_gestion_years, archivo_central_years) "
            "VALUES ('S-1-1', 'subsub', $1, 1, 1)",
            sub,
        )
    # control positivo: el mismo INSERT bajo la SERIE sí pasa
    await conn.execute(
        "INSERT INTO trd_series (code, name, parent_id, archivo_gestion_years, archivo_central_years) "
        "VALUES ('S-2', 'otra sub', $1, 1, 1)",
        serie,
    )


@pytest.mark.asyncio
async def test_una_serie_con_subseries_no_puede_ganar_padre(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    ins = (
        "INSERT INTO trd_series (code, name, archivo_gestion_years, archivo_central_years) "
        "VALUES ($1, $2, 1, 1) RETURNING id"
    )
    padre = await conn.fetchval(ins, "P", "p")
    otra = await conn.fetchval(ins, "O", "o")
    await conn.fetchval(
        "INSERT INTO trd_series (code, name, parent_id, archivo_gestion_years, archivo_central_years) "
        "VALUES ('P-1', 'p1', $1, 1, 1) RETURNING id",
        padre,
    )
    with pytest.raises(asyncpg.CheckViolationError, match="trd_jerarquia_dos_niveles"):
        await conn.execute("UPDATE trd_series SET parent_id = $1 WHERE id = $2", otra, padre)


@pytest.mark.asyncio
async def test_el_servicio_responde_422_antes_que_el_motor(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    serie = await _serie(archive, f"L1{uuid.uuid4().hex[:5]}", [110])
    sub = await archive.create_trd_serie(
        TrdSerieCreate(code=f"L2{uuid.uuid4().hex[:5]}", name="sub", disposition="CT",
                       archivo_gestion_years=1, archivo_central_years=1, parent_id=serie.id),
        actor=None,
    )
    with pytest.raises(HTTPException) as exc:
        await archive.create_trd_serie(
            TrdSerieCreate(code=f"L3{uuid.uuid4().hex[:5]}", name="subsub", disposition="CT",
                           archivo_gestion_years=1, archivo_central_years=1, parent_id=sub.id),
            actor=None,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "trd_jerarquia_dos_niveles"


# ── 070: la productora es contenido de la versión ───────────────────────────────────────


@pytest.mark.asyncio
async def test_la_productora_es_inmutable_fuera_de_borrador(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"IMM{uuid.uuid4().hex[:6]}"
    s = await _serie(archive, code, [110])
    await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)
    await _en(conn, slug)
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute(
            "INSERT INTO trd_serie_dependencia_productora (trd_serie_id, dependencia_codigo, dependencia_nombre) "
            "VALUES ($1, 120, 'x')",
            s.id,
        )
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute("DELETE FROM trd_serie_dependencia_productora WHERE trd_serie_id = $1", s.id)
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute(
            "UPDATE trd_serie_dependencia_productora SET dependencia_nombre = 'otro' WHERE trd_serie_id = $1",
            s.id,
        )
    with pytest.raises(asyncpg.PostgresError, match="TRUNCATE denegado"):
        await conn.execute("TRUNCATE trd_serie_dependencia_productora")


@pytest.mark.asyncio
async def test_la_productora_solo_cuelga_de_la_serie_trd_raiz(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    serie = await conn.fetchval(
        "INSERT INTO trd_series (code, name, archivo_gestion_years, archivo_central_years) "
        "VALUES ('R', 'r', 1, 1) RETURNING id"
    )
    sub = await conn.fetchval(
        "INSERT INTO trd_series (code, name, parent_id, archivo_gestion_years, archivo_central_years) "
        "VALUES ('R-1', 'sub', $1, 1, 1) RETURNING id",
        serie,
    )
    tvd = await conn.fetchval(
        "INSERT INTO trd_series (code, name, tipo_instrumento, fondo_nombre, fecha_extrema_inicial, "
        "fecha_extrema_final, justificacion_valoracion, archivo_central_years) "
        "VALUES ('T', 't', 'TVD', 'F', '1980-01-01', '1990-01-01', 'j', 3) RETURNING id"
    )
    for destino in (sub, tvd):
        with pytest.raises(asyncpg.CheckViolationError, match="productora_solo_en_serie"):
            await conn.execute(
                "INSERT INTO trd_serie_dependencia_productora "
                "(trd_serie_id, dependencia_codigo, dependencia_nombre) VALUES ($1, 110, 'x')",
                destino,
            )
    await conn.execute(  # control positivo: la serie raíz sí
        "INSERT INTO trd_serie_dependencia_productora (trd_serie_id, dependencia_codigo, dependencia_nombre) "
        "VALUES ($1, 110, 'x')",
        serie,
    )


@pytest.mark.asyncio
async def test_ruta_convalidada_se_llena_una_vez_y_solo_sobre_una_version_convalidada(
    tenant_con_organigrama,
) -> None:
    """A9: la ruta describe lo que el Consejo aprobó; antes de convalidar no hay nada que congelar."""
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"RUT{uuid.uuid4().hex[:6]}"
    s = await _serie(archive, code, [110])
    await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)
    await _en(conn, slug)
    fijar = (
        "UPDATE trd_serie_dependencia_productora SET ruta_convalidada = '[{\"codigo\": 100}]'::jsonb "
        "WHERE trd_serie_id = $1"
    )
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute(fijar, s.id)  # aprobada: todavía no hay nada que congelar
    await conn.execute("UPDATE trd_series SET estado = 'convalidada', acto_administrativo = 'R', "
                       "fecha_convalidacion = CURRENT_DATE, instancia_convalidante = 'C' WHERE id = $1", s.id)
    await conn.execute(fijar, s.id)  # convalidada: NULL -> valor, permitido
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute(
            "UPDATE trd_serie_dependencia_productora SET ruta_convalidada = '[]'::jsonb WHERE trd_serie_id = $1",
            s.id,
        )  # ya no: fill-once


@pytest.mark.asyncio
async def test_borrar_un_borrador_se_lleva_su_productora(tenant_con_organigrama) -> None:
    """ON DELETE CASCADE: el trigger no debe bloquear el borrado en cascada de un borrador."""
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    s = await _serie(archive, f"CAS{uuid.uuid4().hex[:6]}", [110, 120])
    await _en(conn, slug)
    await conn.execute("DELETE FROM trd_series WHERE id = $1", s.id)
    assert await conn.fetchval("SELECT count(*) FROM trd_serie_dependencia_productora WHERE trd_serie_id = $1", s.id) == 0


# ── El circuito: gate de productora y ruta congelada ─────────────────────────────────────


@pytest.mark.asyncio
async def test_aprobar_sin_productora_revierte_el_estado(tenant_con_organigrama) -> None:
    """El 409 sale de DENTRO de la transacción: el `UPDATE ... estado='aprobada'` que se hizo
    antes de comprobarlo se revierte. Si el gate estuviera fuera (o la tx no existiera), la
    serie quedaría `aprobada` y sin productora."""
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"SIN{uuid.uuid4().hex[:6]}"
    await _serie(archive, code, None)

    with pytest.raises(HTTPException) as exc:
        await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)

    assert exc.value.status_code == 409 and exc.value.detail["error"] == "trd_serie_sin_productora"
    await _en(conn, slug)
    assert await conn.fetchval("SELECT estado FROM trd_series WHERE code = $1", code) == "borrador"
    assert await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = 'archive.trd_version_aprobada'", slug
    ) == 0


@pytest.mark.asyncio
async def test_convalidar_congela_la_ruta_y_el_cuadro_detecta_la_reorganizacion(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"CCD{uuid.uuid4().hex[:6]}"
    await _serie(archive, code, [110])
    await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)
    conv = await archive.convalidar_trd_serie_version(code, 1, _CONV, actor=None)
    assert conv.estado == "convalidada"

    await _en(conn, slug)
    ruta = json.loads(
        await conn.fetchval(
            "SELECT ruta_convalidada FROM trd_serie_dependencia_productora WHERE trd_serie_id = $1", conv.id
        )
    )
    assert [n["codigo"] for n in ruta] == [100, 110]
    # el asiento lleva la ruta congelada
    cuerpo = await conn.fetchval(
        "SELECT payload FROM public.audit_log WHERE tenant_slug = $1 AND action = 'archive.trd_version_convalidada'",
        slug,
    )
    assert json.loads(cuerpo)["rutas_convalidadas"] == {"110": [100, 110]}

    # el cuadro la ubica bajo 100 > 110 y NO avisa
    cuadro = await archive.get_cuadro_clasificacion()
    sub = cuadro.secciones[0].subsecciones[0]
    assert (cuadro.secciones[0].codigo, sub.codigo) == (100, 110)
    colocada = next(x for x in sub.series if x.code == code)
    assert colocada.ruta_difiere_de_la_convalidada is False
    assert cuadro.fondo.nombre == "Fondo de Desarrollo de Cundinamarca"

    # reestructuración: 110 pasa a colgar de 120. El cuadro lo DICE; no reescribe la versión.
    await conn.execute(
        "UPDATE dependencias SET parent_id = (SELECT id FROM dependencias WHERE codigo = 120) WHERE codigo = 110"
    )
    cuadro = await archive.get_cuadro_clasificacion()
    avisos = {(a.tipo, a.dependencia_codigo) for a in cuadro.avisos}
    assert ("organigrama_reorganizado_tras_convalidacion", 110) in avisos
    ruta_despues = json.loads(
        await conn.fetchval(
            "SELECT ruta_convalidada FROM trd_serie_dependencia_productora WHERE trd_serie_id = $1", conv.id
        )
    )
    assert ruta_despues == ruta  # la instantánea no se tocó


@pytest.mark.asyncio
async def test_una_version_nueva_hereda_la_productora_y_se_convalida_con_su_propia_ruta(
    tenant_con_organigrama,
) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"VER{uuid.uuid4().hex[:6]}"
    await _serie(archive, code, [110])
    await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)
    await archive.convalidar_trd_serie_version(code, 1, _CONV, actor=None)

    v2 = await archive.create_trd_serie_version(code, TrdSerieVersionCreate(archivo_gestion_years=3), actor=None)
    assert [p.dependencia_codigo for p in v2.dependencias_productoras] == [110]
    assert v2.dependencias_productoras[0].ruta_convalidada is None  # la ruta es de CADA convalidación
    assert (v2.archivo_gestion_years, v2.archivo_central_years) == (3, 8)  # la central se clona


@pytest.mark.asyncio
async def test_una_productora_inactiva_no_se_hereda_en_silencio(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"INA{uuid.uuid4().hex[:6]}"
    await _serie(archive, code, [120])
    await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)
    await archive.convalidar_trd_serie_version(code, 1, _CONV, actor=None)
    await _en(conn, slug)
    await conn.execute("UPDATE dependencias SET activo = false WHERE codigo = 120")

    with pytest.raises(HTTPException) as exc:
        await archive.create_trd_serie_version(code, TrdSerieVersionCreate(), actor=None)
    assert exc.value.status_code == 422 and exc.value.detail["error"] == "dependencia_inactiva"

    # y con la lista explícita sí se puede: así entra una productora nueva en una TRD convalidada
    v2 = await archive.create_trd_serie_version(
        code, TrdSerieVersionCreate(dependencias_productoras=[110]), actor=None
    )
    assert [p.dependencia_codigo for p in v2.dependencias_productoras] == [110]


# ── Cuadro: ciclo, vacías, 503 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_ciclo_inyectado_es_409_no_un_cuadro_incompleto(tenant_con_organigrama) -> None:
    """El stub de este tier NO lleva el trigger anticiclos de la 015 (a propósito): se inyecta
    A -> B -> A y el cuadro debe DETECTARLO. Control positivo: sin el ciclo, 200.

    Mutación: quitar `CYCLE id SET es_ciclo USING ruta` de la CTE cambia el desenlace (el tope
    de profundidad lo atrapa como otra cosa, `organigrama_demasiado_profundo`) y este test cae."""
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"CIC{uuid.uuid4().hex[:6]}"
    await _serie(archive, code, [120])
    await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)
    await archive.convalidar_trd_serie_version(code, 1, _CONV, actor=None)
    await _en(conn, slug)

    assert (await archive.get_cuadro_clasificacion()).secciones  # control positivo: sin ciclo, hay cuadro

    await conn.execute(
        "UPDATE dependencias SET parent_id = (SELECT id FROM dependencias WHERE codigo = 120) WHERE codigo = 100"
    )  # 100 -> 120 -> 100
    with pytest.raises(HTTPException) as exc:
        await archive.get_cuadro_clasificacion()
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "organigrama_con_ciclo"
    assert {100, 120} <= set(exc.value.detail["codigos"])


@pytest.mark.asyncio
async def test_un_ciclo_tambien_impide_convalidar(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"CIV{uuid.uuid4().hex[:6]}"
    await _serie(archive, code, [120])
    await archive.aprobar_trd_serie_version(code, 1, _ACTA, actor=None)
    await _en(conn, slug)
    await conn.execute(
        "UPDATE dependencias SET parent_id = (SELECT id FROM dependencias WHERE codigo = 120) WHERE codigo = 100"
    )
    with pytest.raises(HTTPException) as exc:
        await archive.convalidar_trd_serie_version(code, 1, _CONV, actor=None)
    assert exc.value.detail["error"] == "organigrama_con_ciclo"
    assert await conn.fetchval("SELECT estado FROM trd_series WHERE code = $1", code) == "aprobada"


@pytest.mark.asyncio
async def test_el_cuadro_incluye_borradores_y_vacias_solo_si_se_piden(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"BOR{uuid.uuid4().hex[:6]}"
    await _serie(archive, code, [120])  # borrador

    sin = await archive.get_cuadro_clasificacion()
    assert sin.secciones == [] and sin.sin_seccion == []

    con = await archive.get_cuadro_clasificacion(incluir_borradores=True)
    colocada = con.secciones[0].subsecciones[0].series[0]
    assert (colocada.code, colocada.estado) == (code, "borrador")

    def todos(ns):
        for n in ns:
            yield n.codigo
            yield from todos(n.subsecciones)

    vacias = await archive.get_cuadro_clasificacion(incluir_vacias=True)
    assert sorted(todos(vacias.secciones)) == [100, 110, 111, 120]


@pytest.mark.asyncio
async def test_sin_la_tabla_de_dependencias_el_cuadro_es_503(tenant_ctx) -> None:
    slug, conn = tenant_ctx  # SIN organigrama: la tabla de tenant-service no existe
    archive = ArchiveService(conn, slug)
    with pytest.raises(HTTPException) as exc:
        await archive.get_cuadro_clasificacion()
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "catalogo_dependencias_no_disponible"


# ── 071 y C10: `publicable` propio del tipo ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publicable_nace_falso_y_es_independiente_de_la_ventanilla(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    svc = TipoDocumentalService(conn, slug)
    t = await svc.create(TipoDocumentalCreate(code="OFI", nombre="Oficio").model_dump(), actor=None)
    assert (t.publicable, t.admite_ventanilla_publica) == (False, False)

    t = await svc.update(
        t.id, TipoDocumentalUpdate(admite_ventanilla_publica=True).model_dump(exclude_unset=True), actor=None
    )
    assert (t.publicable, t.admite_ventanilla_publica) == (False, True)  # no se acoplan

    t = await svc.update(t.id, TipoDocumentalUpdate(publicable=True).model_dump(exclude_unset=True), actor=None)
    assert (t.publicable, t.admite_ventanilla_publica) == (True, True)
    await _en(conn, slug)
    acciones = [
        r["action"]
        for r in await conn.fetch(
            "SELECT action FROM public.audit_log WHERE tenant_slug = $1 AND object_ref = 'OFI' ORDER BY id", slug
        )
    ]
    assert acciones.count("archive.tipo_documental_publicacion_cambiada") == 1


@pytest.mark.asyncio
async def test_desmarcar_cuenta_los_radicados_aun_publicados_o_dice_que_no_sabe(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    svc = TipoDocumentalService(conn, slug)
    t = await svc.create(TipoDocumentalCreate(code="RES", nombre="Resolución", publicable=True).model_dump(), actor=None)
    await _en(conn, slug)

    # `radicados` no existe en este schema (es de document-service): «no sé», jamás «cero».
    sin_tabla = await svc.update(t.id, {"publicable": False}, actor=None)
    assert sin_tabla.publicados_vigentes is None

    t = await svc.update(t.id, {"publicable": True}, actor=None)
    await conn.execute(
        "CREATE TABLE radicados (id SERIAL PRIMARY KEY, doc_class TEXT, publicable BOOLEAN NOT NULL DEFAULT false)"
    )
    await conn.execute(
        "INSERT INTO radicados (doc_class, publicable) VALUES ('RES', true), ('RES', true), ('RES', false), ('OTRO', true)"
    )
    con_tabla = await svc.update(t.id, {"publicable": False}, actor=None)
    assert con_tabla.publicados_vigentes == 2  # solo los de ESTE tipo y publicados


# ── A1: la ratificación de una versión `migrada` captura la productora ──────────────────


async def _migrada_sin_productora(conn, archive, code: str):
    s = await _serie(archive, code, None)
    await conn.execute(
        f'UPDATE "{tenant_schema(archive._tenant_slug)}".trd_series SET estado = \'migrada\' WHERE id = $1', s.id
    )
    return s


@pytest.mark.asyncio
async def test_ratificar_una_migrada_sin_productora_la_declara_y_la_congela(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"RAT{uuid.uuid4().hex[:6]}"
    s = await _migrada_sin_productora(conn, archive, code)

    # sin productora, la ratificación sigue cerrada y la versión NO cambia de estado
    with pytest.raises(HTTPException) as exc:
        await archive.convalidar_trd_serie_version(code, 1, _CONV, actor=None)
    assert exc.value.detail["error"] == "trd_serie_sin_productora"
    await _en(conn, slug)
    assert await conn.fetchval("SELECT estado FROM trd_series WHERE id = $1", s.id) == "migrada"

    # con ella, en la MISMA transacción: la declara, pasa el gate y congela la ruta
    conv = await archive.convalidar_trd_serie_version(
        code, 1, _CONV.model_copy(update={"dependencias_productoras": [110, 120]}), actor=None
    )
    assert conv.estado == "convalidada"
    filas = await conn.fetch(
        "SELECT dependencia_codigo, ruta_convalidada FROM trd_serie_dependencia_productora "
        "WHERE trd_serie_id = $1 ORDER BY 1", s.id
    )
    assert [f["dependencia_codigo"] for f in filas] == [110, 120]
    assert all(f["ruta_convalidada"] is not None for f in filas)
    acciones = [
        r["action"]
        for r in await conn.fetch(
            "SELECT action FROM public.audit_log WHERE tenant_slug = $1 AND object_ref = $2 ORDER BY id", slug, code
        )
    ]
    # la alta de la serie (helper) ya asienta; lo que se prueba es lo que la ratificación añade
    assert acciones == [
        "archive.trd_serie_created",
        "archive.trd_productora_declarada",
        "archive.trd_version_convalidada",
    ]


@pytest.mark.asyncio
async def test_si_la_ratificacion_falla_la_productora_declarada_se_revierte(tenant_con_organigrama) -> None:
    """Misma transacción: un ciclo en el organigrama aborta la convalidación y la productora
    recién insertada no se queda (la versión sigue `migrada` y sin productora)."""
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    code = f"RAR{uuid.uuid4().hex[:6]}"
    s = await _migrada_sin_productora(conn, archive, code)
    await _en(conn, slug)
    await conn.execute(
        "UPDATE dependencias SET parent_id = (SELECT id FROM dependencias WHERE codigo = 120) WHERE codigo = 100"
    )
    with pytest.raises(HTTPException) as exc:
        await archive.convalidar_trd_serie_version(
            code, 1, _CONV.model_copy(update={"dependencias_productoras": [120]}), actor=None
        )
    assert exc.value.detail["error"] == "organigrama_con_ciclo"
    assert await conn.fetchval("SELECT count(*) FROM trd_serie_dependencia_productora WHERE trd_serie_id = $1", s.id) == 0
    assert await conn.fetchval("SELECT estado FROM trd_series WHERE id = $1", s.id) == "migrada"


@pytest.mark.asyncio
async def test_en_una_migrada_la_productora_solo_se_anade_nunca_se_cambia(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    archive = ArchiveService(conn, slug)
    s = await _migrada_sin_productora(conn, archive, f"ADD{uuid.uuid4().hex[:6]}")
    await _en(conn, slug)
    ins = ("INSERT INTO trd_serie_dependencia_productora (trd_serie_id, dependencia_codigo, dependencia_nombre) "
           "VALUES ($1, $2, 'x')")
    await conn.execute(ins, s.id, 110)  # añadir: permitido
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute("DELETE FROM trd_serie_dependencia_productora WHERE trd_serie_id = $1", s.id)
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute("UPDATE trd_serie_dependencia_productora SET dependencia_nombre = 'y' WHERE trd_serie_id = $1", s.id)
    # ya ratificada (convalidada) tampoco se añade
    await conn.execute("UPDATE trd_series SET estado = 'convalidada', acto_administrativo = 'R', "
                       "fecha_convalidacion = CURRENT_DATE, instancia_convalidante = 'C' WHERE id = $1", s.id)
    with pytest.raises(asyncpg.CheckViolationError, match="trd_productora_inmutable"):
        await conn.execute(ins, s.id, 120)


# ── A7: retención mínima ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_motor_rechaza_una_trd_de_cero_mas_cero(tenant_con_organigrama) -> None:
    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    with pytest.raises(asyncpg.CheckViolationError, match="trd_series_retencion_minima_check"):
        await conn.execute(
            "INSERT INTO trd_series (code, name, archivo_gestion_years, archivo_central_years) VALUES ('Z', 'z', 0, 0)"
        )
    await conn.execute(  # control: 0 + 1 es válida
        "INSERT INTO trd_series (code, name, archivo_gestion_years, archivo_central_years) VALUES ('Z1', 'z', 0, 1)"
    )


# ── S3: el tope de dos niveles bajo concurrencia ────────────────────────────────────────


@pytest.mark.asyncio
async def test_dos_cambios_concurrentes_no_pueden_formar_tres_niveles(tenant_con_organigrama) -> None:
    """T1: alta de una subserie bajo S. T2: S pasa a ser subserie de X. Cada una, vista sola, es
    válida; juntas dan X > S > hija. Sin el candado de la 069 las dos confirman.
    Mutación: quitar `pg_advisory_xact_lock` de la función cae este test."""
    import asyncio

    slug, conn = tenant_con_organigrama
    await _en(conn, slug)
    ins = ("INSERT INTO trd_series (code, name, archivo_gestion_years, archivo_central_years) "
           "VALUES ($1::text, $1::text, 1, 1) RETURNING id")
    s_id = await conn.fetchval(ins, "S")
    x_id = await conn.fetchval(ins, "X")
    from tests.integration.conftest import _require_dsn

    async def abrir() -> asyncpg.Connection:
        c = await asyncpg.connect(dsn=_require_dsn())
        await c.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        return c

    async def alta_hija() -> str:
        c = await abrir()
        try:
            async with c.transaction():
                await c.execute(
                    "INSERT INTO trd_series (code, name, parent_id, archivo_gestion_years, archivo_central_years) "
                    "VALUES ('S-1', 's1', $1, 1, 1)", s_id)
                await asyncio.sleep(0.8)
            return "confirmado"
        except asyncpg.CheckViolationError:
            return "rechazado"
        finally:
            await c.close()

    async def s_pasa_a_subserie() -> str:
        c = await abrir()
        try:
            await asyncio.sleep(0.25)
            async with c.transaction():
                await c.execute("UPDATE trd_series SET parent_id = $1 WHERE id = $2", x_id, s_id)
            return "confirmado"
        except asyncpg.CheckViolationError:
            return "rechazado"
        finally:
            await c.close()

    r1, r2 = await asyncio.gather(alta_hija(), s_pasa_a_subserie())
    assert sorted([r1, r2]) == ["confirmado", "rechazado"], (r1, r2)


# ── S4: la dependencia no se desactiva mientras se escribe su productora ─────────────────


@pytest.mark.asyncio
async def test_la_dependencia_comprobada_queda_bloqueada_hasta_el_commit(tenant_con_organigrama) -> None:
    import asyncio

    from app.repositories.archive_repository import ArchiveRepository
    from tests.integration.conftest import _require_dsn

    slug, conn = tenant_con_organigrama
    otra = await asyncpg.connect(dsn=_require_dsn())
    try:
        await otra.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        await otra.execute("SET lock_timeout = '400ms'")
        async with conn.transaction():
            await ArchiveRepository(conn, slug).lookup_dependencias([110])  # FOR SHARE
            with pytest.raises(asyncpg.LockNotAvailableError):
                await otra.execute("UPDATE dependencias SET activo = false WHERE codigo = 110")
        # liberada al commit: ahora sí puede (control positivo)
        await otra.execute("UPDATE dependencias SET activo = false WHERE codigo = 110")
        await asyncio.sleep(0)
    finally:
        await otra.close()
