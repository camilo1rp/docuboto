# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# ruff: noqa: F811
# (los fixtures importados de ADR-028 se «redefinen» al usarse como argumento: así funciona pytest)

"""ADR-029 contra Postgres REAL: dónde está el radicado AHORA.

Lo que un mock no puede probar: que la ubicación se escribe en la MISMA transacción
que el paso y el estado (y revierte con ellos); que la columna GENERADA
`ubicacion_*` sale del `COALESCE` por pares.

Carril de integración (`RUN_INTEGRATION=1`, ver `conftest.py`).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from tests.integration.conftest import insert_dependencia

# Los fixtures y ayudas del carril de ADR-028 (mismo arnés: RBAC concedido, Redis
# mock, radicador del reparto). Importarlos es la forma de compartir fixtures
# entre módulos sin moverlos a un conftest que los aplicaría a todo el carril.
from tests.integration.test_estado_radicado_integration import (  # noqa: F401
    DEP_ARC,
    DEP_JUR,
    _asientos,
    _assign,
    _radicado,
    _sin_permisos_ni_redis,
    _user,
    ctx,
)

pytestmark = pytest.mark.integration

TEC = (300, "Tecnología")
TH = (400, "Talento Humano")
CI = (500, "Control Interno")


async def _dependencias(conn: asyncpg.Connection, *filas: tuple[int, str, bool]) -> None:
    for codigo, nombre, activo in filas:
        await insert_dependencia(conn, codigo, nombre, activo)


async def _radicado_en(
    conn: asyncpg.Connection, dep: tuple[int, str] | None, status: str = "registered"
) -> uuid.UUID:
    """Radicado cuya dependencia de RADICACIÓN es `dep` (dest_dept/dest_dept_code)."""
    rid = await _radicado(conn, status)
    if dep is not None:
        await conn.execute(
            "UPDATE radicados SET dest_dept = $2, dest_dept_code = $3 WHERE id = $1",
            rid, dep[1], dep[0],
        )
    return rid


async def _ubic(conn: asyncpg.Connection, rid: uuid.UUID) -> dict[str, Any]:
    return dict(
        await conn.fetchrow(
            "SELECT dest_dept, dest_dept_code, flujo_dept, flujo_dept_code, flujo_usuario, "
            "ubicacion_dept, ubicacion_dept_code FROM radicados WHERE id = $1",
            rid,
        )
    )


# ---------------------------------------------------------------------------
# El caso real: 2026-DEMO-E-000117
# ---------------------------------------------------------------------------


async def test_radicar_en_tecnologia_repartir_a_talento_humano_y_devolver_a_control_interno(ctx: Any) -> None:
    svc, conn, _ = ctx
    await _dependencias(conn, (*TEC, True), (*TH, True), (*CI, True))
    rid = await _radicado_en(conn, TEC)
    rad, u_th = await _user(conn, TEC[0]), await _user(conn, TH[0])

    # Sin pasos: la ubicación es la dependencia de radicación.
    u = await _ubic(conn, rid)
    assert (u["ubicacion_dept"], u["ubicacion_dept_code"], u["flujo_dept"]) == (TEC[1], 300, None)

    await svc.assign(_assign(rid, TH[1], u_th), rad)
    u = await _ubic(conn, rid)
    assert (u["ubicacion_dept"], u["ubicacion_dept_code"], u["flujo_usuario"]) == (TH[1], 400, u_th)

    await svc.devolver(rid, to_dept=CI[1], causal="No corresponde", comentario=None, actor=u_th)

    u = await _ubic(conn, rid)
    assert (u["ubicacion_dept"], u["ubicacion_dept_code"]) == (CI[1], 500)
    assert (u["flujo_dept"], u["flujo_dept_code"], u["flujo_usuario"]) == (CI[1], 500, None)
    # La dependencia de RADICACIÓN es una instantánea legal: no se movió.
    assert (u["dest_dept"], u["dest_dept_code"]) == (TEC[1], 300)


async def test_rollback_vuelve_a_la_dependencia_anterior(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid = await _radicado(conn)
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.transfer(_assign(rid, DEP_ARC[1], u2, action="transfer", from_dept=DEP_JUR[1]), u1)
    assert (await _ubic(conn, rid))["flujo_dept"] == DEP_ARC[1]

    await svc.rollback(rid, u2, motivo="Asignada por error")

    u = await _ubic(conn, rid)
    assert (u["flujo_dept"], u["flujo_usuario"]) == (DEP_JUR[1], u1)


async def test_rollback_y_luego_completar_se_queda_donde_se_completo(ctx: Any) -> None:
    """El paso cancelado por el rollback tiene el número MÁS ALTO pero se cerró
    ANTES: ordenar el «último» por `step_number` lo pondría en Archivo."""
    svc, conn, _ = ctx
    from workflow.schemas.workflow import FlowStepComplete

    rid = await _radicado(conn)
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    paso1 = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.transfer(_assign(rid, DEP_ARC[1], u2, action="transfer", from_dept=DEP_JUR[1]), u1)
    await svc.rollback(rid, u2, motivo="Asignada por error")

    await svc.complete_step(paso1.id, FlowStepComplete(), u1)

    u = await _ubic(conn, rid)
    assert (u["flujo_dept"], u["flujo_usuario"]) == (DEP_JUR[1], u1)
    assert await conn.fetchval(
        "SELECT status FROM flow_steps WHERE radicado_id = $1 AND step_number = 2", rid
    ) == "cancelled"


async def test_finalizar_deja_el_radicado_en_la_dependencia_donde_se_cerro(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u1)

    u = await _ubic(conn, rid)
    assert await conn.fetchval("SELECT status FROM radicados WHERE id = $1", rid) == "closed"
    assert (u["ubicacion_dept"], u["flujo_usuario"]) == (DEP_JUR[1], u1)


async def test_la_anulacion_congela_la_ubicacion(ctx: Any) -> None:
    """La anulación la escribe document-service (estado `anulado` y paso cancelado
    con `completed_at`). Nada posterior mueve la ubicación, ni siquiera un paso
    que apareciera después."""
    svc, conn, _ = ctx
    rid = await _radicado(conn)
    u1 = await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await conn.execute(
        "UPDATE flow_steps SET status = 'cancelled', completed_at = now() WHERE radicado_id = $1", rid
    )
    await conn.execute("UPDATE radicados SET status = 'anulado' WHERE id = $1", rid)
    antes = await _ubic(conn, rid)
    # Un paso tardío hacia otra dependencia (p. ej. un escritor que no respeta el 409).
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, to_dept_code, "
        "assigned_by, action, status) SELECT id, tracking_number, 9, $2, $3, gen_random_uuid(), "
        "'assign', 'pending' FROM radicados WHERE id = $1",
        rid, DEP_ARC[1], DEP_ARC[0],
    )

    assert await svc._repo.sincronizar_estado_radicado(rid) == ("anulado", "anulado")

    assert await _ubic(conn, rid) == antes
    assert antes["flujo_dept"] == DEP_JUR[1]


async def test_la_cascada_cambia_el_poseedor_y_no_la_dependencia(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid = await _radicado(conn)
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    assert (await _ubic(conn, rid))["flujo_usuario"] == u1

    await svc.reassign_cascade(from_assigned_to=u1, from_dept=None, to_assigned_to=u2, actor=u1)

    u = await _ubic(conn, rid)
    assert (u["flujo_dept"], u["flujo_usuario"]) == (DEP_JUR[1], u2)


async def test_la_cascada_no_reescribe_la_ubicacion_de_un_terminal(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid = await _radicado(conn)
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await conn.execute("UPDATE radicados SET status = 'closed' WHERE id = $1", rid)
    antes = await _ubic(conn, rid)

    res = await svc.reassign_cascade(from_assigned_to=u1, from_dept=None, to_assigned_to=u2, actor=u1)

    assert res.skipped_terminal == 1
    assert await _ubic(conn, rid) == antes


# ---------------------------------------------------------------------------
# Nombre sin código único: código NULL + instantánea del nombre
# ---------------------------------------------------------------------------


async def test_nombre_duplicado_no_se_desempata_y_el_codigo_del_paso_fija_la_ubicacion(ctx: Any) -> None:
    """ADR-032: por NOMBRE, dos homónimas son 422 (antes: código NULL); por CÓDIGO la
    ubicación es exacta y el par (nombre, código) sale de la misma dependencia."""
    from fastapi import HTTPException

    svc, conn, _ = ctx
    await _dependencias(conn, (*TEC, True), (610, "Duplicada", True), (611, "Duplicada", True))
    rid = await _radicado_en(conn, TEC)
    u = await _user(conn, 610)

    with pytest.raises(HTTPException) as exc:
        await svc.assign(_assign(rid, "Duplicada", u), u)
    assert exc.value.detail["error"] == "dependencia_ambigua"
    assert (await _ubic(conn, rid))["flujo_dept"] is None  # nada se escribió

    await svc.assign(_assign(rid, "Duplicada", u, to_dept_code=611), u)

    r = await _ubic(conn, rid)
    assert (r["flujo_dept"], r["flujo_dept_code"]) == ("Duplicada", 611)
    assert (r["ubicacion_dept"], r["ubicacion_dept_code"]) == ("Duplicada", 611)


async def test_una_dependencia_inactiva_con_el_mismo_nombre_no_estorba(ctx: Any) -> None:
    svc, conn, _ = ctx
    await _dependencias(conn, (620, "Solo", False), (621, "Solo", True))
    rid = await _radicado(conn)
    u = await _user(conn, 621)

    await svc.assign(_assign(rid, "Solo", u), u)

    assert (await _ubic(conn, rid))["flujo_dept_code"] == 621


# ---------------------------------------------------------------------------
# Atomicidad
# ---------------------------------------------------------------------------


async def test_si_la_auditoria_falla_no_queda_ubicacion(ctx: Any) -> None:
    svc, conn, _ = ctx
    await _dependencias(conn, (*TEC, True))
    rid = await _radicado_en(conn, TEC)
    u = await _user(conn, DEP_JUR[0])
    antes = await _ubic(conn, rid)

    with (
        patch("workflow.services.workflow_service.audit.append", new=AsyncMock(side_effect=RuntimeError("audit caído"))),
        pytest.raises(RuntimeError, match="audit caído"),
    ):
        await svc.assign(_assign(rid, DEP_JUR[1], u), u)

    assert await _ubic(conn, rid) == antes
    assert antes["flujo_dept"] is None and antes["ubicacion_dept"] == TEC[1]


# ---------------------------------------------------------------------------
# La evidencia de recálculo de ubicación es de SOLO inserción
# ---------------------------------------------------------------------------


async def test_la_evidencia_de_recalculo_de_ubicacion_es_append_only(ctx: Any) -> None:
    _, conn, _ = ctx
    rid = await _radicado(conn)
    await conn.execute(
        "INSERT INTO radicado_ubicacion_recalculo (radicado_id, tracking_number, estado_radicado) "
        "VALUES ($1, 'T-1', 'distributed')",
        rid,
    )
    for sql in ("UPDATE radicado_ubicacion_recalculo SET antes_dept = 'x'", "DELETE FROM radicado_ubicacion_recalculo"):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute(sql)
