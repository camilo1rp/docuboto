# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cierre del lote C (migración 076) contra Postgres REAL: selección (S) por expediente,
`anexo_sha256` NOT NULL en la baja de soporte y lote avalado que no se borra.

Mutaciones que deben caer:
  * quitar el trigger que exige la selección -> `test_una_valoracion_s_no_se_aplica_sin_seleccion`;
  * dejar mutable la selección -> `test_la_seleccion_es_fill_once`;
  * quitar el trigger de DELETE -> `test_un_lote_avalado_no_se_borra`;
  * quitar el NOT NULL -> `test_la_baja_de_soporte_exige_huella`.
"""

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.valoracion_repository import ValoracionRepository
from tests.integration.test_c5_c7_valoracion_sql import _expediente, _hold, _tvd

pytestmark = pytest.mark.integration


async def _valorada(conn, slug, disposicion: str):
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug, disposition=disposicion)
    exp = await _expediente(conn)
    await _hold(conn, exp, tvd.id)
    repo = ValoracionRepository(conn, slug)
    val = await repo.insert_valoracion(
        trd_serie_id=tvd.id, disposicion=disposicion, acta_comite="A", fecha_acta=date(2026, 1, 1),
        fundamento="x" * 25, decidida_por=uuid.uuid4(),
        metodo_muestreo="Muestreo sistematico: uno de cada diez" if disposicion == "S" else None,
    )
    await repo.amarrar_valoracion(tvd.id, val["id"])
    return repo, tvd, exp


async def test_una_valoracion_s_exige_el_metodo_de_muestreo(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    tvd = await _tvd(conn, slug, disposition="S")
    with pytest.raises(asyncpg.CheckViolationError):
        await ValoracionRepository(conn, slug).insert_valoracion(
            trd_serie_id=tvd.id, disposicion="S", acta_comite="A", fecha_acta=date(2026, 1, 1),
            fundamento="x" * 25, decidida_por=uuid.uuid4(), metodo_muestreo=None,
        )


async def test_una_valoracion_s_no_se_aplica_sin_seleccion(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, tvd, exp = await _valorada(conn, slug, "S")
    assert await repo.get_para_aplicar(exp) is None, "S sin selección: no aparece como aplicable"
    assert await repo.list_pendientes_aplicar(10) == [] or all(
        r["expediente_id"] != exp for r in await repo.list_pendientes_aplicar(10)
    )
    conteos = await repo.conteos(tvd.id)
    assert conteos["seleccion_pendiente"] == 1 and conteos["pendientes"] == 0
    with pytest.raises(asyncpg.RaiseError):  # el motor lo impide aunque se salte el servicio
        await conn.execute(
            "UPDATE expediente_valoracion_hold SET aplicada_at = now(), resultado = 'sin_retencion' "
            "WHERE expediente_id = $1", exp,
        )


async def test_la_seleccion_es_fill_once_y_habilita_la_aplicacion(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo, tvd, exp = await _valorada(conn, slug, "S")
    fila = await repo.registrar_seleccion(
        exp, accion="conservar", fundamento="Entra a la muestra", actor=uuid.uuid4()
    )
    assert fila is not None and fila["seleccion"] == "conservar"
    assert await repo.registrar_seleccion(
        exp, accion="descartar", fundamento="Otro criterio distinto", actor=uuid.uuid4()
    ) is None, "no se rehace"
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            "UPDATE expediente_valoracion_hold SET seleccion = 'descartar' WHERE expediente_id = $1", exp
        )
    aplicable = await repo.get_para_aplicar(exp)
    assert aplicable is not None and aplicable["seleccion"] == "conservar"
    assert (await repo.conteos(tvd.id))["seleccion_pendiente"] == 0


async def test_la_seleccion_exige_fundamento(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    _, _, exp = await _valorada(conn, slug, "S")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "UPDATE expediente_valoracion_hold SET seleccion = 'conservar', seleccion_por = gen_random_uuid(), "
            "seleccion_at = now(), seleccion_fundamento = 'corto' WHERE expediente_id = $1", exp,
        )


async def test_un_lote_avalado_no_se_borra(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    borrador = await conn.fetchval(
        "INSERT INTO eliminacion_lotes (codigo, creado_por) VALUES ($1, gen_random_uuid()) RETURNING id",
        f"L-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute("DELETE FROM eliminacion_lotes WHERE id = $1", borrador)  # borrador: sí
    avalado = await conn.fetchval(
        "INSERT INTO eliminacion_lotes (codigo, creado_por) VALUES ($1, gen_random_uuid()) RETURNING id",
        f"L-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        "UPDATE eliminacion_lotes SET estado = 'avalado', acta_comite = 'Acta 1', "
        "fecha_aval = current_date, acto_administrativo = 'Res. 1', avalado_por = gen_random_uuid(), "
        "avalado_at = now() WHERE id = $1",
        avalado,
    )
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute("DELETE FROM eliminacion_lotes WHERE id = $1", avalado)


async def test_la_baja_de_soporte_exige_huella(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    nullable = await conn.fetchval(
        "SELECT is_nullable FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'eliminacion_lote_soporte_citado' AND column_name = 'anexo_sha256'"
    )
    tiene_check = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_lote_soporte_sha256' "
        "AND conrelid = 'eliminacion_lote_soporte_citado'::regclass"
    )
    assert nullable == "NO" or tiene_check == 1
