# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Integración (Postgres REAL) del trigger de tenencia física (migración 065, B4).

Un INSERT directo —sin pasar por `TransferenciaService._validar_ubicaciones`— no puede dejar
un par de ubicaciones sin tenencia que el acta FUID atestiguaría como movimiento real. Cada
rama del trigger es una prueba: borrar la rama (mutación) deja pasar su caso.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.actor import Actor
from app.schemas.transferencia import TransferenciaCreate
from app.services.transferencia_service import TransferenciaService

from .test_atomicity import _seed_expediente_closed
from .test_transferencia_creacion_integridad import _make_ubicacion
from .test_transferencia_tenencia_fisica import _make_unidad_linked

_INSERT = (
    "INSERT INTO transferencias (tipo, expediente_id, ubicacion_origen_id, ubicacion_destino_id, "
    "creada_por, objeto) VALUES ($1, $2, $3, $4, 'itest', 'Transferencia de prueba del trigger') "
    "RETURNING id"
)


async def _debe_fallar(conn: asyncpg.Connection, codigo: str, *args) -> None:
    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await conn.fetchval(_INSERT, *args)
    assert exc.value.message == codigo
    assert exc.value.sqlstate == "23514"


@pytest.mark.integration
async def test_sin_tenencia_el_par_es_rechazado_y_la_logica_pasa(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    exp = await _seed_expediente_closed(conn, slug, "itest trigger sin tenencia")
    o = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    d = await _make_ubicacion(conn, slug, tipo_archivo="central")
    await _debe_fallar(conn, "ubicacion_sin_tenencia", "primaria", exp.id, o.id, d.id)
    await _debe_fallar(conn, "ubicaciones_par_incompleto", "primaria", exp.id, o.id, None)
    await _debe_fallar(conn, "ubicaciones_par_incompleto", "primaria", exp.id, None, d.id)
    # lógica (sin par): pasa
    assert await conn.fetchval(_INSERT, "primaria", exp.id, None, None) is not None


@pytest.mark.integration
async def test_con_tenencia_el_par_es_obligatorio_y_se_coteja(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    exp = await _seed_expediente_closed(conn, slug, "itest trigger con tenencia")
    actual, _u = await _make_unidad_linked(conn, slug, exp.id, tipo_archivo="gestion")
    ajena = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    central = await _make_ubicacion(conn, slug, tipo_archivo="central")
    gestion2 = await _make_ubicacion(conn, slug, tipo_archivo="gestion")

    await _debe_fallar(conn, "ubicaciones_requeridas_fisico", "primaria", exp.id, None, None)
    await _debe_fallar(conn, "ubicaciones_requeridas_fisico", "primaria", exp.id, actual.id, None)
    await _debe_fallar(
        conn, "ubicaciones_origen_destino_iguales", "primaria", exp.id, actual.id, actual.id
    )
    await _debe_fallar(conn, "ubicacion_origen_no_actual", "primaria", exp.id, ajena.id, central.id)
    # origen actual pero destino del tipo equivocado (gestion en vez de central)
    await _debe_fallar(
        conn, "ubicacion_incoherente_con_tipo", "primaria", exp.id, actual.id, gestion2.id
    )
    # secundaria exige origen 'central': el origen actual es 'gestion'
    await _debe_fallar(
        conn, "ubicacion_incoherente_con_tipo", "secundaria", exp.id, actual.id, central.id
    )
    # el caso válido pasa
    assert await conn.fetchval(_INSERT, "primaria", exp.id, actual.id, central.id) is not None


@pytest.mark.integration
async def test_cambiar_de_estado_y_borrar_una_ubicacion_no_revalidan(tenant_ctx) -> None:
    """Solo filas nuevas / columnas de ubicación tocadas: `enviar`/`recibir` no disparan el
    trigger, y la acción referencial `ON DELETE SET NULL` (que solo anula) tampoco."""
    slug, conn = tenant_ctx
    exp = await _seed_expediente_closed(conn, slug, "itest trigger update")
    actual, _unidad = await _make_unidad_linked(conn, slug, exp.id, tipo_archivo="gestion")
    central = await _make_ubicacion(conn, slug, tipo_archivo="central")
    tid = await conn.fetchval(_INSERT, "primaria", exp.id, actual.id, central.id)
    # la unidad se mueve: la tenencia que sustentaba la fila ya no existe, y la fila heredada
    # sigue siendo operable
    await conn.execute("DELETE FROM expediente_unidad WHERE expediente_id = $1", exp.id)
    await conn.execute("UPDATE transferencias SET estado = 'enviada' WHERE id = $1", tid)
    await conn.execute("DELETE FROM ubicacion WHERE id = $1", central.id)
    fila = await conn.fetchrow("SELECT estado, ubicacion_destino_id FROM transferencias WHERE id = $1", tid)
    assert fila["estado"] == "enviada" and fila["ubicacion_destino_id"] is None


@pytest.mark.integration
async def test_el_servicio_traduce_el_check_a_422_con_el_mismo_codigo(tenant_ctx) -> None:
    """Defensa en profundidad: si una carrera deja pasar el cotejo del servicio, la base
    responde y el 422 lleva el mismo código."""
    slug, conn = tenant_ctx
    exp = await _seed_expediente_closed(conn, slug, "itest trigger 422")
    o = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    d = await _make_ubicacion(conn, slug, tipo_archivo="central")
    svc = TransferenciaService(conn, slug)
    with patch.object(svc, "_validar_ubicaciones", new=AsyncMock()), pytest.raises(HTTPException) as exc:
        await svc.create(
            TransferenciaCreate(
                tipo="primaria",
                objeto="Transferencia primaria de la serie de contratos",
                expediente_id=exp.id,
                ubicacion_origen_id=o.id,
                ubicacion_destino_id=d.id,
            ),
            Actor(id=uuid.uuid4(), username="itest-trigger"),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "ubicacion_sin_tenencia"


@pytest.mark.integration
async def test_un_update_manual_que_anula_el_par_se_valida_y_el_set_null_de_ri_no(tenant_ctx) -> None:
    """Migración 067. Mutación: quitar `pg_trigger_depth() > 1` (la exención vuelve a cubrir un
    UPDATE manual) => el UPDATE que borra la tenencia pasa y esta prueba cae."""
    slug, conn = tenant_ctx
    exp = await _seed_expediente_closed(conn, slug, "itest trigger update manual")
    actual, _unidad = await _make_unidad_linked(conn, slug, exp.id, tipo_archivo="gestion")
    central = await _make_ubicacion(conn, slug, tipo_archivo="central")
    tid = await conn.fetchval(_INSERT, "primaria", exp.id, actual.id, central.id)

    for sentencia in (
        "UPDATE transferencias SET ubicacion_destino_id = NULL WHERE id = $1",
        "UPDATE transferencias SET ubicacion_origen_id = NULL WHERE id = $1",
        "UPDATE transferencias SET ubicacion_origen_id = NULL, ubicacion_destino_id = NULL WHERE id = $1",
    ):
        with pytest.raises(asyncpg.CheckViolationError) as exc:
            await conn.execute(sentencia, tid)
        assert exc.value.sqlstate == "23514"
    # un UPDATE que repite los mismos valores no afirma nada nuevo: pasa
    await conn.execute(
        "UPDATE transferencias SET ubicacion_destino_id = ubicacion_destino_id WHERE id = $1", tid
    )
    # la acción referencial (borrar la ubicación de una transferencia no recibida) sigue anulando
    await conn.execute("DELETE FROM ubicacion WHERE id = $1", central.id)
    assert await conn.fetchval("SELECT ubicacion_destino_id FROM transferencias WHERE id = $1", tid) is None


@pytest.mark.integration
async def test_no_se_borra_una_ubicacion_con_transferencia_recibida(tenant_ctx) -> None:
    """Migración 068. Mutación: quitar el trigger (o su filtro `estado='recibida'`): el SET NULL
    de la 012 borraría la traza física de lo ya recibido."""
    slug, conn = tenant_ctx
    exp = await _seed_expediente_closed(conn, slug, "itest ubicacion recibida")
    actual, _unidad = await _make_unidad_linked(conn, slug, exp.id, tipo_archivo="gestion")
    central = await _make_ubicacion(conn, slug, tipo_archivo="central")
    tid = await conn.fetchval(_INSERT, "primaria", exp.id, actual.id, central.id)
    await conn.execute("UPDATE transferencias SET estado = 'recibida' WHERE id = $1", tid)

    for ubic in (central.id, actual.id):  # destino y origen
        with pytest.raises(asyncpg.CheckViolationError) as exc:
            await conn.execute("DELETE FROM ubicacion WHERE id = $1", ubic)
        assert exc.value.message == "ubicacion_con_transferencia_recibida"
        assert exc.value.sqlstate == "23514"
    fila = await conn.fetchrow(
        "SELECT ubicacion_origen_id, ubicacion_destino_id FROM transferencias WHERE id = $1", tid
    )
    assert (fila["ubicacion_origen_id"], fila["ubicacion_destino_id"]) == (actual.id, central.id)

    # control positivo: la misma ubicación SIN transferencia recibida sí se borra
    libre = await _make_ubicacion(conn, slug, tipo_archivo="central")
    await conn.execute("DELETE FROM ubicacion WHERE id = $1", libre.id)
    assert await conn.fetchval("SELECT count(*) FROM ubicacion WHERE id = $1", libre.id) == 0
