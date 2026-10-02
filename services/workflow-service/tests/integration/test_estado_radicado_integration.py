# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-028 contra Postgres REAL: el flujo mueve el estado del radicado.

Lo que un mock no puede probar: que el paso, el estado del radicado, el evento
operativo y el asiento commitean o revierten JUNTOS; que un radicado terminal no
cambia ni un recuento; que el cerrojo de tenencia lo decide la base; y que el
backfill SQL coincide fila a fila con la función pura.

Carril de integración (`RUN_INTEGRATION=1`, ver `conftest.py`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn, tenant_schema
from app.main import app
from app.schemas.workflow import FlowStepComplete, FlowStepCreate, TransactionRequest
from app.services.estado_radicado import derivar_estado_radicado
from app.services.workflow_service import (
    RollbackError,
    TransactionError,
    WorkflowService,
    YaDistribuidoError,
)
from migrations import runner

pytestmark = pytest.mark.integration

DEP_JUR = ("100", "Jurídica")
DEP_ARC = ("200", "Archivo")


# ---------------------------------------------------------------------------
# Fixtures y ayudas
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _sin_permisos_ni_redis() -> Any:
    """El RBAC (`check_permission`) y Redis no son el SUT: el permiso se concede y
    Redis es un mock cuyas llamadas se inspeccionan."""
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value="1-0")
    with (
        patch.object(WorkflowService, "_require_permission", new=AsyncMock(return_value=None)),
        patch("app.services.workflow_service.get_redis", return_value=redis),
    ):
        yield redis


class _ServicioConRadicador(WorkflowService):
    """Convención del ARNÉS (no del producto): el primer reparto de un radicado sin
    pasos solo lo hace quien lo radicó (`radicados.registered_by`, [B]). Los tests
    previos reparten con «el usuario que sea» sobre radicados con `registered_by`
    NULL; para no reescribirlos, el arnés registra como radicador a quien reparte
    primero SOLO si `registered_by` es NULL. Un test que fija `registered_by` (los
    de [B]) no se ve afectado, y uno que quiere probar el rechazo lo hace con un
    radicado de OTRO radicador o con `llamar_sin_arnes`."""

    async def _create_step(self, payload: Any, assigned_by: uuid.UUID, *, sistema: bool = False) -> Any:
        if not sistema:
            # Solo lo que el actor alcanza: el arnés no debe «radicar» un reservado
            # a nombre de quien no puede verlo.
            nivel = await self._repo.clearance_para_acto(assigned_by)
            await self._conn.execute(
                "UPDATE radicados SET registered_by = $2 WHERE id = $1 AND registered_by IS NULL "
                "AND nivel_seguridad <= $3 "
                "AND NOT EXISTS (SELECT 1 FROM flow_steps WHERE radicado_id = $1)",
                payload.radicado_id,
                assigned_by,
                nivel,
            )
        return await super()._create_step(payload, assigned_by, sistema=sistema)


@pytest_asyncio.fixture
async def ctx(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> AsyncGenerator[tuple[WorkflowService, asyncpg.Connection, str], None]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    for cod, nombre in (DEP_JUR, DEP_ARC):
        await conn.execute("INSERT INTO dependencias (codigo, nombre) VALUES ($1, $2)", cod, nombre)
    yield _ServicioConRadicador(conn, slug), conn, slug


async def _user(conn: asyncpg.Connection, dep: str | None = None) -> uuid.UUID:
    uid = await conn.fetchval("INSERT INTO auth_users DEFAULT VALUES RETURNING id")
    if dep is not None:
        await conn.execute("INSERT INTO urd_usuaroldep (user_id, depe_id) VALUES ($1, $2)", uid, dep)
    return uid


_seq = iter(range(1, 10_000))


async def _radicado(conn: asyncpg.Connection, status: str = "registered") -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, status) VALUES ($1, $2) RETURNING id",
        f"2026-ITEST-E-{next(_seq):06d}",
        status,
    )


async def _estado(conn: asyncpg.Connection, rid: uuid.UUID) -> str:
    return await conn.fetchval("SELECT status FROM radicados WHERE id = $1", rid)


async def _conteos(conn: asyncpg.Connection, slug: str) -> tuple[int, int, int]:
    return (
        await conn.fetchval("SELECT count(*) FROM flow_steps"),
        await conn.fetchval("SELECT count(*) FROM flow_events"),
        await conn.fetchval("SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1", slug),
    )


async def _asientos(conn: asyncpg.Connection, slug: str, action: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT object_ref, payload FROM public.audit_log "
        "WHERE tenant_slug = $1 AND action = $2 ORDER BY id",
        slug,
        action,
    )
    return [{"object_ref": r["object_ref"], "payload": json.loads(r["payload"])} for r in rows]


def _assign(rid: uuid.UUID, dept: str, assigned_to: uuid.UUID | None, **kw: Any) -> FlowStepCreate:
    return FlowStepCreate(
        radicado_id=rid,
        tracking_number="ignorado",
        to_dept=dept,
        assigned_to=assigned_to,
        action=kw.pop("action", "assign"),
        **kw,
    )


async def _tracking(conn: asyncpg.Connection, rid: uuid.UUID) -> str:
    return await conn.fetchval("SELECT tracking_number FROM radicados WHERE id = $1", rid)


# ---------------------------------------------------------------------------
# assign -> distributed, y atomicidad
# ---------------------------------------------------------------------------


async def test_assign_pasa_a_distributed_y_deja_asiento(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, actor = await _radicado(conn), await _user(conn)

    step = await svc.assign(_assign(rid, DEP_JUR[1], None), actor)

    assert step.status == "pending"
    assert await _estado(conn, rid) == "distributed"
    (asiento,) = await _asientos(conn, slug, "workflow.assign")
    assert asiento["payload"]["estado_radicado"] == {"antes": "registered", "despues": "distributed"}


async def test_assign_emite_estado_cambiado_tras_commit(ctx: Any, _sin_permisos_ni_redis: Any) -> None:
    svc, conn, _ = ctx
    rid, actor = await _radicado(conn), await _user(conn)
    await svc.assign(_assign(rid, DEP_JUR[1], None), actor)

    tipos = [c.args[1]["event_type"] for c in _sin_permisos_ni_redis.xadd.call_args_list]
    assert tipos == ["workflow.step.created", "workflow.radicado.estado_cambiado"]
    evt = _sin_permisos_ni_redis.xadd.call_args_list[1].args[1]
    assert evt["dedup_key"].startswith(f"{rid}:distributed:")


async def test_fallo_forzado_de_audit_no_deja_ni_paso_ni_estado(
    ctx: Any, _sin_permisos_ni_redis: Any
) -> None:
    svc, conn, slug = ctx
    rid, actor = await _radicado(conn), await _user(conn)
    antes = await _conteos(conn, slug)

    with (
        patch(
            "app.services.workflow_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("audit caído")),
        ),
        pytest.raises(RuntimeError, match="audit caído"),
    ):
        await svc.assign(_assign(rid, DEP_JUR[1], None), actor)

    assert await _conteos(conn, slug) == antes
    assert await _estado(conn, rid) == "registered"
    # Y NO se emitió ningún evento (la emisión es posterior al commit).
    _sin_permisos_ni_redis.xadd.assert_not_awaited()


async def test_sincronizar_radicado_inexistente_es_none(ctx: Any) -> None:
    svc, conn, _ = ctx
    assert await svc._repo.sincronizar_estado_radicado(uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# marcar_leido -> in_progress, solo el poseedor
# ---------------------------------------------------------------------------


async def test_marcar_leido_del_poseedor_pasa_a_in_progress(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), u1)

    assert await _estado(conn, rid) == "in_progress"
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE radicado_id = $1", rid) == "in_progress"
    ultimo = (await _asientos(conn, slug, "workflow.transaccion"))[-1]
    assert ultimo["payload"]["estado_radicado"] == {"antes": "distributed", "despues": "in_progress"}


async def test_marcar_leido_sin_ser_poseedor_404_y_nada_cambia(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid = await _radicado(conn)
    u1 = await _user(conn, DEP_JUR[0])
    colega = await _user(conn, DEP_JUR[0])  # misma dependencia, pero el paso es de u1
    ajeno = await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    antes = await _conteos(conn, slug)

    for intruso in (colega, ajeno):
        with pytest.raises(HTTPException) as exc:
            await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), intruso)
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "no_active_step"

    assert await _conteos(conn, slug) == antes
    assert await _estado(conn, rid) == "distributed"


async def test_paso_de_dependencia_sin_dueno_lo_posee_quien_pertenece_a_ella(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid = await _radicado(conn)
    miembro, ajeno = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], None), miembro)

    with pytest.raises(HTTPException) as exc:
        await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), ajeno)
    assert exc.value.status_code == 404

    await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), miembro)
    assert await _estado(conn, rid) == "in_progress"


async def test_requires_holder_solo_en_las_transacciones_que_mueven_el_paso(ctx: Any) -> None:
    """`marcar_leido` (migración 012) y `anular` (014): las dos que tienen
    `target_step_status`. El resto del catálogo no mueve el paso."""
    _, conn, _ = ctx
    filas = await conn.fetch("SELECT code FROM transaction_types WHERE requires_holder ORDER BY code")
    assert [r["code"] for r in filas] == ["anular", "marcar_leido"]
    mueven = await conn.fetch(
        "SELECT code FROM transaction_types WHERE target_step_status IS NOT NULL ORDER BY code"
    )
    assert [r["code"] for r in mueven] == ["anular", "marcar_leido"]


# ---------------------------------------------------------------------------
# devolver / rollback / transfer -> distributed
# ---------------------------------------------------------------------------


async def test_devolver_vuelve_a_distributed(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), u1)
    assert await _estado(conn, rid) == "in_progress"

    await svc.devolver(rid, to_dept=DEP_ARC[1], causal="No corresponde", comentario=None, actor=u1)

    assert await _estado(conn, rid) == "distributed"
    (asiento,) = await _asientos(conn, slug, "workflow.devolver")
    assert asiento["payload"]["estado_radicado"] == {"antes": "in_progress", "despues": "distributed"}


async def test_rollback_vuelve_a_distributed(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid = await _radicado(conn)
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.transfer(_assign(rid, DEP_ARC[1], u2, action="transfer", from_dept=DEP_JUR[1]), u1)
    # Forzado a mano: el rollback debe RECALCULAR, no limitarse a dejar lo que había.
    await conn.execute("UPDATE radicados SET status = 'in_progress' WHERE id = $1", rid)

    await svc.rollback(rid, u2, motivo="Asignada por error")

    assert await _estado(conn, rid) == "distributed"


async def test_complete_sin_marcar_leido_deja_distributed(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    step = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    await svc.complete_step(step.id, FlowStepComplete(), u1)

    assert await _estado(conn, rid) == "distributed"  # declarado en ADR-028


async def test_reparto_sin_paso_deja_registered(ctx: Any) -> None:
    _, conn, _ = ctx
    rid = await _radicado(conn)
    assert await _estado(conn, rid) == "registered"


# ---------------------------------------------------------------------------
# finalizar -> closed, y luego todo 409 sin tocar un recuento
# ---------------------------------------------------------------------------


async def test_finalizar_cierra_y_luego_cada_acto_da_409_sin_cambiar_recuentos(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    step = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    cerrado = await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u1)

    assert cerrado.status == "completed"
    assert await _estado(conn, rid) == "closed"
    (asiento,) = await _asientos(conn, slug, "workflow.finalizar")
    assert asiento["payload"]["estado_radicado"] == {"antes": "distributed", "despues": "closed"}
    assert await conn.fetchval(
        "SELECT count(*) FROM flow_events WHERE radicado_id = $1 AND tipo_tx = 'finalizar'", rid
    ) == 1

    antes = await _conteos(conn, slug)

    async def _409(coro: Any) -> None:
        with pytest.raises(HTTPException) as exc:
            await coro
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "radicado_terminal"

    # Un cerrado ya no tiene paso activo NI «último paso» que reparta ([B]: terminal
    # excluido de `ultimo_paso_si_lo_poseia`): el reparto da el 404 neutro, no el 409.
    await _404(svc.assign(_assign(rid, DEP_ARC[1], None), u1), "no_active_step")
    await _404(
        svc.transfer(_assign(rid, DEP_ARC[1], None, action="transfer", from_dept=DEP_JUR[1]), u1),
        "no_active_step",
    )

    # Sin paso activo: completar el paso ya cerrado, `anular` (exige tenencia,
    # ADR-028 remediación), devolver y finalizar dan el 404 neutro —un cerrado no
    # revela su estado a quien no posee nada—; rollback, su 409 propio.
    with pytest.raises(HTTPException) as exc:
        await svc.complete_step(step.id, FlowStepComplete(), u1)
    assert (exc.value.status_code, exc.value.detail["error"]) == (404, "step_not_found")
    with pytest.raises(HTTPException) as exc:
        await svc.execute_transaction(rid, TransactionRequest(tipo_tx="anular"), u1)
    assert (exc.value.status_code, exc.value.detail["error"]) == (404, "no_active_step")
    with pytest.raises(HTTPException) as exc:
        await svc.devolver(rid, to_dept=DEP_ARC[1], causal="x", comentario=None, actor=u1)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await svc.finalizar(rid, comentario="Otra vez", motivo_cierre="respondido", actor=u1)
    assert exc.value.status_code == 404
    with pytest.raises(RollbackError):
        await svc.rollback(rid, u1, motivo="Intento tardío")

    assert await _conteos(conn, slug) == antes
    assert await _estado(conn, rid) == "closed"
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", step.id) == "completed"


async def test_informar_sigue_permitido_sobre_un_cerrado(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u1)

    await svc.execute_transaction(rid, TransactionRequest(tipo_tx="informar"), u1)

    assert await _estado(conn, rid) == "closed"


async def test_finalizar_solo_el_poseedor(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid = await _radicado(conn)
    u1, ajeno = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    antes = await _conteos(conn, slug)

    with pytest.raises(HTTPException) as exc:
        await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=ajeno)

    assert exc.value.status_code == 404
    assert await _conteos(conn, slug) == antes
    assert await _estado(conn, rid) == "distributed"


# ---------------------------------------------------------------------------
# anulado es terminal (lo escribe la anulación; el paso sigue activo)
# ---------------------------------------------------------------------------


async def test_anulado_es_terminal_y_nada_cambia(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    step = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    # La anulación (document-service) escribe el estado y NO cancela el paso.
    await conn.execute("UPDATE radicados SET status = 'anulado' WHERE id = $1", rid)
    antes = await _conteos(conn, slug)

    async def _409(coro: Any) -> None:
        with pytest.raises(HTTPException) as exc:
            await coro
        assert exc.value.status_code == 409

    await _409(svc.assign(_assign(rid, DEP_ARC[1], None), u1))
    await _409(svc.complete_step(step.id, FlowStepComplete(), u1))
    await _409(svc.devolver(rid, to_dept=DEP_ARC[1], causal="x", comentario=None, actor=u1))
    await _409(svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u1))
    await _409(svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), u1))
    await _409(svc.execute_transaction(rid, TransactionRequest(tipo_tx="anular"), u1))

    assert await _conteos(conn, slug) == antes
    assert await _estado(conn, rid) == "anulado"
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", step.id) == "pending"


# ---------------------------------------------------------------------------
# reasignación en cascada
# ---------------------------------------------------------------------------


async def test_cascada_devuelve_a_pending_excluye_terminales_y_asienta_por_radicado(ctx: Any) -> None:
    svc, conn, slug = ctx
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_JUR[0])
    leido, sin_leer, cerrado, anulado = [await _radicado(conn) for _ in range(4)]
    for rid in (leido, sin_leer, cerrado, anulado):
        await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.execute_transaction(leido, TransactionRequest(tipo_tx="marcar_leido"), u1)
    await conn.execute("UPDATE radicados SET status = 'closed' WHERE id = $1", cerrado)
    await conn.execute("UPDATE radicados SET status = 'anulado' WHERE id = $1", anulado)
    antes_asientos = len(await _asientos(conn, slug, "workflow.estado_radicado"))

    res = await svc.reassign_cascade(
        from_assigned_to=u1, from_dept=None, to_assigned_to=u2, actor=u1
    )

    assert (res.reassigned, res.skipped_terminal) == (2, 2)
    for rid in (leido, sin_leer):
        paso = await conn.fetchrow("SELECT status, assigned_to FROM flow_steps WHERE radicado_id = $1", rid)
        assert (paso["status"], paso["assigned_to"]) == ("pending", u2)
        assert await _estado(conn, rid) == "distributed"
    for rid, est in ((cerrado, "closed"), (anulado, "anulado")):
        paso = await conn.fetchrow("SELECT status, assigned_to FROM flow_steps WHERE radicado_id = $1", rid)
        assert (paso["status"], paso["assigned_to"]) == ("pending", u1)  # intacto
        assert await _estado(conn, rid) == est
    # Un asiento `estado_radicado` SOLO por el que cambió (`leido`: in_progress->distributed).
    nuevos = (await _asientos(conn, slug, "workflow.estado_radicado"))[antes_asientos:]
    assert [(a["object_ref"], a["payload"]["antes"], a["payload"]["despues"]) for a in nuevos] == [
        (await _tracking(conn, leido), "in_progress", "distributed")
    ]
    (casc,) = await _asientos(conn, slug, "workflow.reasignacion_cascada")
    assert casc["payload"]["reassigned"] == 2 and casc["payload"]["skipped_terminal"] == 2


# ---------------------------------------------------------------------------
# backfill (013): fila a fila contra la función pura, e idempotente
# ---------------------------------------------------------------------------


async def _paso(conn: asyncpg.Connection, rid: uuid.UUID, n: int, status: str) -> None:
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, "
        "assigned_by, action, status) SELECT id, tracking_number, $2, 'Jurídica', "
        "gen_random_uuid(), 'assign', $3 FROM radicados WHERE id = $1",
        rid,
        n,
        status,
    )


async def test_backfill_coincide_con_la_funcion_pura_y_es_idempotente(ctx: Any) -> None:
    _, conn, slug = ctx
    casos: list[tuple[str, list[str]]] = [
        ("registered", []),
        ("registered", ["pending"]),
        ("registered", ["in_progress"]),
        ("registered", ["completed"]),
        ("registered", ["cancelled", "returned"]),
        ("distributed", []),
        ("distributed", ["pending"]),
        ("distributed", ["in_progress"]),
        ("distributed", ["completed"]),
        ("in_progress", ["pending"]),
        ("in_progress", ["completed"]),
        ("in_progress", ["completed", "pending"]),
        ("in_progress", ["pending", "in_progress"]),  # anómalo: 2 activos, gana el último
        ("closed", ["pending"]),
        ("closed", ["completed"]),
        ("anulado", ["in_progress"]),
        ("anulado", []),
    ]
    ids: list[uuid.UUID] = []
    for estado, pasos in casos:
        rid = await _radicado(conn, estado)
        for n, st in enumerate(pasos, start=1):
            await _paso(conn, rid, n, st)
        ids.append(rid)
    sql = next(p for p in runner._sql_files(runner.TENANT_DIR) if "radicado_estado_desde_flujo" in p.name)

    async def _correr() -> None:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path = "{tenant_schema(slug)}", public')
            await conn.execute(sql.read_text())

    previos = {rid: await _estado(conn, rid) for rid in ids}
    await _correr()

    for rid in ids:
        pasos = await conn.fetch(
            "SELECT status FROM flow_steps WHERE radicado_id = $1 "
            "AND status IN ('pending','in_progress') ORDER BY step_number DESC",
            rid,
        )
        tiene = bool(await conn.fetchval("SELECT count(*) FROM flow_steps WHERE radicado_id = $1", rid))
        esperado = derivar_estado_radicado(
            previos[rid], pasos[0]["status"] if pasos else None, tiene_pasos=tiene
        )
        assert await _estado(conn, rid) == esperado, (previos[rid], [p["status"] for p in pasos])

    evidencia = await conn.fetch("SELECT radicado_id, antes, despues FROM radicado_estado_recalculo")
    cambiados = {rid for rid in ids if await _estado(conn, rid) != previos[rid]}
    assert {e["radicado_id"] for e in evidencia} == cambiados
    for e in evidencia:
        assert (e["antes"], e["despues"]) == (previos[e["radicado_id"]], await _estado(conn, e["radicado_id"]))
    # Terminales intactos.
    for rid in ids:
        if previos[rid] in ("closed", "anulado"):
            assert await _estado(conn, rid) == previos[rid]

    # Segunda corrida: 0 filas cambiadas y 0 evidencia nueva.
    foto = {rid: await _estado(conn, rid) for rid in ids}
    n_evidencia = len(evidencia)
    await _correr()
    assert {rid: await _estado(conn, rid) for rid in ids} == foto
    assert await conn.fetchval("SELECT count(*) FROM radicado_estado_recalculo") == n_evidencia


# ===========================================================================
# Garantías declaradas en el ADR-028 (fijadas para que nadie las cambie sin querer)
# ===========================================================================

Conexiones = Callable[[], Awaitable[asyncpg.Connection]]
_EVT_ESTADO = "workflow.radicado.estado_cambiado"


def _evt_estado(redis: Any) -> list[dict[str, Any]]:
    """Eventos `estado_cambiado` que recibió el XADD del doble de Redis, con el
    payload ya decodificado."""
    out: list[dict[str, Any]] = []
    for c in redis.xadd.call_args_list:
        campos = c.args[1]
        if campos["event_type"] == _EVT_ESTADO:
            payload = campos["payload"]
            out.append(
                {
                    "dedup_key": campos["dedup_key"],
                    "payload": json.loads(payload) if isinstance(payload, str) else payload,
                }
            )
    return out


async def _snapshot_pasos(conn: asyncpg.Connection) -> list[tuple[Any, ...]]:
    rows = await conn.fetch(
        "SELECT id, step_number, status, assigned_to, to_dept FROM flow_steps ORDER BY id"
    )
    return [tuple(r) for r in rows]


@pytest_asyncio.fixture
async def http(ctx: Any) -> AsyncGenerator[tuple[AsyncClient, asyncpg.Connection, str], None]:
    """Cliente HTTP in-process cuya dependencia de BD es la conexión REAL del
    tenant desechable: lo que se fija aquí es lo que ve el cliente (códigos y
    cuerpos de error), no solo lo que lanza el servicio. El RBAC se concede (no
    es el SUT; ya lo cubre el carril mock)."""
    _, conn, slug = ctx

    async def _db() -> AsyncGenerator[asyncpg.Connection, None]:
        yield conn

    app.dependency_overrides[get_db_conn] = _db
    try:
        with (
            patch("app.core.clearance.check_permission", new=AsyncMock(return_value=True)),
            patch("app.services.workflow_service.check_permission", new=AsyncMock(return_value=True)),
            # El clearance real vive en tablas de auth-service que este tier no
            # provisiona; aquí se concede el máximo (no es el SUT).
            patch(
                "app.repositories.workflow_repository.resolve_caller_clearance",
                new=AsyncMock(return_value=5),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield c, conn, slug
    finally:
        app.dependency_overrides.clear()


def _h(slug: str, user: uuid.UUID, canal: str | None = None) -> dict[str, str]:
    h = {"X-Tenant-Slug": slug, "X-User-Id": str(user)}
    if canal:
        h["X-Canal"] = canal
    return h


def _error(resp: Any) -> str:
    cuerpo = resp.json()
    return str((cuerpo.get("detail") or cuerpo)["error"] if "error" not in cuerpo else cuerpo["error"])


async def _circuito_dos_pasos(
    svc: WorkflowService, conn: asyncpg.Connection
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, Any]:
    """Radicado con 2 pasos: JUR (u1, completado) -> ARC (u2, pending, vigente).
    Devuelve (rid, u1, u2, paso vigente)."""
    rid = await _radicado(conn)
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    paso = await svc.transfer(_assign(rid, DEP_ARC[1], u2, action="transfer", from_dept=DEP_JUR[1]), u1)
    return rid, u1, u2, paso


# ---------------------------------------------------------------------------
# Códigos declarados: cerrado -> 404 neutro / rollback 409 propio; anulado -> 409 en todos
# ---------------------------------------------------------------------------


async def test_http_cerrado_finalizar_y_devolver_404_neutro_rollback_409_no_active_step(http: Any) -> None:
    c, conn, slug = http
    svc = _ServicioConRadicador(conn, slug)
    rid, _u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u2)
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn))

    r = await c.post(
        f"/api/v1/workflows/{rid}/finalizar", json={"comentario": "Otra vez", "motivo_cierre": "otro"}, headers=_h(slug, u2)
    )
    assert (r.status_code, _error(r)) == (404, "no_active_step")
    r = await c.post(
        f"/api/v1/workflows/{rid}/devolver",
        json={"to_dept": DEP_JUR[1], "causal": "x"},
        headers=_h(slug, u2),
    )
    assert (r.status_code, _error(r)) == (404, "no_active_step")
    r = await c.post(
        f"/api/v1/workflows/{rid}/rollback", json={"motivo": "Intento tardío"}, headers=_h(slug, u2)
    )
    assert (r.status_code, _error(r)) == (409, "no_active_step")

    # El reparto sobre un cerrado también es el 404 neutro ([B]: ya no hay «último paso»
    # repartible; antes daba el 409 terminal).
    cuerpo_paso = {
        "radicado_id": str(rid),
        "tracking_number": "ignorado",
        "to_dept": DEP_JUR[1],
        "assigned_to": str(u2),
        "action": "assign",
    }
    r = await c.post("/api/v1/workflows/assign", json=cuerpo_paso, headers=_h(slug, u2))
    assert (r.status_code, _error(r)) == (404, "no_active_step")
    r = await c.post(
        "/api/v1/workflows/transfer",
        json={**cuerpo_paso, "action": "transfer", "from_dept": DEP_ARC[1]},
        headers=_h(slug, u2),
    )
    assert (r.status_code, _error(r)) == (404, "no_active_step")
    # Un paso ya cerrado no se re-cierra (404 `step_not_found`) y `anular` exige
    # tenencia: sin paso activo, 404 neutro y no 409 (oráculo cerrado, ADR-028).
    r = await c.patch(f"/api/v1/workflows/{paso.id}/complete", json={}, headers=_h(slug, u2))
    assert (r.status_code, _error(r)) == (404, "step_not_found")
    r = await c.post(
        f"/api/v1/workflows/{rid}/transactions", json={"tipo_tx": "anular"}, headers=_h(slug, u2)
    )
    assert (r.status_code, _error(r)) == (404, "no_active_step")
    # `marcar_leido` exige tenencia y un cerrado no tiene paso activo: 404 neutro, no 409.
    r = await c.post(
        f"/api/v1/workflows/{rid}/transactions", json={"tipo_tx": "marcar_leido"}, headers=_h(slug, u2)
    )
    assert (r.status_code, _error(r)) == (404, "no_active_step")

    assert (await _conteos(conn, slug), await _snapshot_pasos(conn)) == antes
    assert await _estado(conn, rid) == "closed"


async def test_http_anulado_con_paso_activo_409_radicado_terminal_en_todos_los_actos(
    http: Any, _sin_permisos_ni_redis: Any
) -> None:
    c, conn, slug = http
    svc = _ServicioConRadicador(conn, slug)
    rid, _u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    # La anulación (document-service) escribe el estado y NO cancela el paso.
    await conn.execute("UPDATE radicados SET status = 'anulado' WHERE id = $1", rid)
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn))
    _sin_permisos_ni_redis.xadd.reset_mock()
    cuerpo_paso = {
        "radicado_id": str(rid),
        "tracking_number": "ignorado",
        "to_dept": DEP_JUR[1],
        "assigned_to": str(u2),
        "action": "assign",
    }
    h = _h(slug, u2)
    llamadas = [
        ("post", f"/api/v1/workflows/{rid}/finalizar", {"comentario": "Trámite concluido", "motivo_cierre": "respondido"}),
        ("post", f"/api/v1/workflows/{rid}/devolver", {"to_dept": DEP_JUR[1], "causal": "x"}),
        ("post", f"/api/v1/workflows/{rid}/rollback", {"motivo": "Intento sobre anulado"}),
        ("post", "/api/v1/workflows/assign", cuerpo_paso),
        ("post", "/api/v1/workflows/transfer", {**cuerpo_paso, "action": "transfer", "from_dept": DEP_ARC[1]}),
        ("patch", f"/api/v1/workflows/{paso.id}/complete", {}),
        ("post", f"/api/v1/workflows/{rid}/transactions", {"tipo_tx": "marcar_leido"}),
        ("post", f"/api/v1/workflows/{rid}/transactions", {"tipo_tx": "anular"}),
    ]
    for metodo, url, cuerpo in llamadas:
        r = await getattr(c, metodo)(url, json=cuerpo, headers=h)
        assert (r.status_code, _error(r)) == (409, "radicado_terminal"), (url, cuerpo, r.text)
        # Cada intento revirtió TODO (si no, el siguiente vería otro estado de pasos).
        assert (await _conteos(conn, slug), await _snapshot_pasos(conn)) == antes, (url, cuerpo)

    assert await _estado(conn, rid) == "anulado"
    # El 409 es terminal también para el bus: ningún evento salió de un acto revertido.
    _sin_permisos_ni_redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# Carrera: anulación vs assign
# ---------------------------------------------------------------------------


async def test_carrera_anulacion_vs_assign_no_deja_paso_nuevo_sobre_el_anulado(
    ctx: Any, nueva_conexion: Conexiones, _sin_permisos_ni_redis: Any
) -> None:
    """La anulación (otra sesión) UPDATEa el radicado y aún no commitea cuando
    `assign` abre su paso. `assign` debe quedar esperando la fila del radicado,
    y al commitear la anulación ver `anulado` y abortar: ningún paso nuevo."""
    svc, conn, slug = ctx
    rid, actor = await _radicado(conn), await _user(conn)
    anuladora = await nueva_conexion()
    await anuladora.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    tx = anuladora.transaction()
    await tx.start()
    commiteada = False
    tarea: asyncio.Task[Any] | None = None
    try:
        await anuladora.execute("UPDATE radicados SET status = 'anulado' WHERE id = $1", rid)
        tarea = asyncio.create_task(svc.assign(_assign(rid, DEP_JUR[1], None), actor))
        pid = conn.get_server_pid()
        for _ in range(200):
            espera = await anuladora.fetchval(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = $1", pid
            )
            if espera == "Lock":
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("assign no quedó esperando la fila del radicado: la carrera no se reprodujo")
        assert not tarea.done()
        await tx.commit()
        commiteada = True
        with pytest.raises(HTTPException) as exc:
            await asyncio.wait_for(tarea, timeout=10)
    finally:
        if not commiteada:
            await tx.rollback()
        if tarea is not None and not tarea.done():
            tarea.cancel()

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "radicado_terminal"
    assert await conn.fetchval("SELECT count(*) FROM flow_steps WHERE radicado_id = $1", rid) == 0
    assert await conn.fetchval("SELECT count(*) FROM flow_events WHERE radicado_id = $1", rid) == 0
    assert await _estado(conn, rid) == "anulado"
    _sin_permisos_ni_redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# Evento workflow.radicado.estado_cambiado: tras el commit, y no si revierte
# ---------------------------------------------------------------------------


async def test_estado_cambiado_se_emite_solo_tras_commit_visible_desde_otra_sesion(
    ctx: Any, nueva_conexion: Conexiones, _sin_permisos_ni_redis: Any
) -> None:
    svc, conn, slug = ctx
    rid, actor = await _radicado(conn), await _user(conn)
    observador = await nueva_conexion()
    vistos: list[tuple[str, int]] = []

    async def _xadd(_stream: str, campos: dict[str, Any]) -> str:
        if campos["event_type"] == _EVT_ESTADO:
            vistos.append(
                (
                    await observador.fetchval(
                        f'SELECT status FROM "{tenant_schema(slug)}".radicados WHERE id = $1', rid
                    ),
                    await observador.fetchval(
                        f'SELECT count(*) FROM "{tenant_schema(slug)}".flow_steps WHERE radicado_id = $1',
                        rid,
                    ),
                )
            )
        return "1-0"

    _sin_permisos_ni_redis.xadd.side_effect = _xadd
    await svc.assign(_assign(rid, DEP_JUR[1], None), actor)

    # Al emitir, OTRA sesión ya ve el estado nuevo y el paso: el evento salió tras el COMMIT.
    assert vistos == [("distributed", 1)]


async def test_estado_cambiado_por_cada_acto_y_ninguno_si_no_hay_cambio(
    ctx: Any, _sin_permisos_ni_redis: Any
) -> None:
    _, conn, slug = ctx
    rid = await _radicado(conn)
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])

    def nuevo() -> WorkflowService:
        # Una instancia por acto, como hace cada request: con una compartida, el
        # evento de un acto podría salir en el `_emitir_cambios` del siguiente y
        # una emisión omitida quedaría enmascarada.
        return _ServicioConRadicador(conn, slug)

    await nuevo().assign(_assign(rid, DEP_JUR[1], u1), u1)  # registered -> distributed
    await nuevo().transfer(
        _assign(rid, DEP_ARC[1], u2, action="transfer", from_dept=DEP_JUR[1]), u1
    )  # distributed -> distributed: SIN evento
    await nuevo().execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), u2)
    await nuevo().devolver(rid, to_dept=DEP_JUR[1], causal="No corresponde", comentario=None, actor=u2)
    miembro = await _user(conn, DEP_JUR[0])
    await nuevo().execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), miembro)
    await nuevo().finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=miembro)  # -> closed

    eventos = _evt_estado(_sin_permisos_ni_redis)
    assert [(e["payload"]["antes"], e["payload"]["despues"]) for e in eventos] == [
        ("registered", "distributed"),
        ("distributed", "in_progress"),
        ("in_progress", "distributed"),
        ("distributed", "in_progress"),
        ("in_progress", "closed"),
    ]
    for e in eventos:
        assert e["payload"]["radicado_id"] == str(rid)
        assert e["dedup_key"].startswith(f"{rid}:{e['payload']['despues']}:")
    # La clave de deduplicación referencia un flow_event REAL, distinto en cada cambio.
    assert len({e["dedup_key"] for e in eventos}) == 5
    for e in eventos:
        fe = uuid.UUID(e["dedup_key"].rsplit(":", 1)[1])
        assert await conn.fetchval("SELECT count(*) FROM flow_events WHERE id = $1", fe) == 1


async def test_rollback_y_cascada_emiten_estado_cambiado(ctx: Any, _sin_permisos_ni_redis: Any) -> None:
    svc, conn, slug = ctx
    rid, u1, u2, _paso = await _circuito_dos_pasos(svc, conn)
    await conn.execute("UPDATE radicados SET status = 'in_progress' WHERE id = $1", rid)
    _sin_permisos_ni_redis.xadd.reset_mock()

    await WorkflowService(conn, slug).rollback(rid, u2, motivo="Asignada por error")

    eventos = _evt_estado(_sin_permisos_ni_redis)
    assert [(e["payload"]["radicado_id"], e["payload"]["antes"], e["payload"]["despues"]) for e in eventos] == [
        (str(rid), "in_progress", "distributed")
    ]

    leido = await _radicado(conn)
    await svc.assign(_assign(leido, DEP_JUR[1], u1), u1)
    await svc.execute_transaction(leido, TransactionRequest(tipo_tx="marcar_leido"), u1)
    sin_leer = await _radicado(conn)
    await svc.assign(_assign(sin_leer, DEP_JUR[1], u1), u1)
    _sin_permisos_ni_redis.xadd.reset_mock()

    await WorkflowService(conn, slug).reassign_cascade(
        from_assigned_to=u1, from_dept=None, to_assigned_to=u2, actor=u1
    )

    # Solo cambió de estado el que estaba leído (in_progress -> distributed).
    eventos = _evt_estado(_sin_permisos_ni_redis)
    assert [(e["payload"]["radicado_id"], e["payload"]["antes"], e["payload"]["despues"]) for e in eventos] == [
        (str(leido), "in_progress", "distributed")
    ]


async def test_estado_cambiado_no_se_emite_si_la_transaccion_revierte(
    ctx: Any, _sin_permisos_ni_redis: Any
) -> None:
    """Un fallo DESPUÉS de sincronizar (audit) revierte el acto y no emite nada:
    ni `estado_cambiado` ni `step.created`."""
    svc, conn, slug = ctx
    rid, actor = await _radicado(conn), await _user(conn)
    antes = await _conteos(conn, slug)

    with (
        patch("app.services.workflow_service.audit.append", new=AsyncMock(side_effect=RuntimeError("audit"))),
        pytest.raises(RuntimeError),
    ):
        await svc.assign(_assign(rid, DEP_JUR[1], None), actor)
    assert await _conteos(conn, slug) == antes
    assert await _estado(conn, rid) == "registered"
    _sin_permisos_ni_redis.xadd.assert_not_awaited()

    # Con un servicio nuevo (como hace cada request), el acto siguiente emite UN evento: el suyo.
    await _ServicioConRadicador(conn, slug).assign(_assign(rid, DEP_JUR[1], None), actor)
    assert len(_evt_estado(_sin_permisos_ni_redis)) == 1


async def test_cambio_de_un_acto_revertido_no_se_emite_en_el_siguiente_acto_de_la_misma_instancia(
    ctx: Any, _sin_permisos_ni_redis: Any
) -> None:
    svc, conn, _ = ctx
    rid, actor = await _radicado(conn), await _user(conn)
    with (
        patch("app.services.workflow_service.audit.append", new=AsyncMock(side_effect=RuntimeError("audit"))),
        pytest.raises(RuntimeError),
    ):
        await svc.assign(_assign(rid, DEP_JUR[1], None), actor)

    await svc.assign(_assign(rid, DEP_JUR[1], None), actor)

    assert len(_evt_estado(_sin_permisos_ni_redis)) == 1


async def test_fallo_de_redis_no_aborta_el_acto_ya_commiteado(ctx: Any, _sin_permisos_ni_redis: Any) -> None:
    svc, conn, _ = ctx
    rid, actor = await _radicado(conn), await _user(conn)
    _sin_permisos_ni_redis.xadd.side_effect = ConnectionError("redis caído")

    step = await svc.assign(_assign(rid, DEP_JUR[1], None), actor)

    assert step.status == "pending"
    assert await _estado(conn, rid) == "distributed"


# ---------------------------------------------------------------------------
# Un colega de la misma dependencia VE lo asignado a otro, pero no lo POSEE
# ---------------------------------------------------------------------------


async def test_colega_de_la_misma_dependencia_no_puede_actuar_sobre_el_paso_de_otro(ctx: Any) -> None:
    svc, conn, slug = ctx
    u0 = await _user(conn, DEP_ARC[0])
    u1 = await _user(conn, DEP_JUR[0])
    colega = await _user(conn, DEP_JUR[0])  # misma dependencia que u1, paso asignado a u1
    rid = await _radicado(conn)
    await svc.assign(_assign(rid, DEP_ARC[1], u0), u0)
    paso = await svc.transfer(_assign(rid, DEP_JUR[1], u1, action="transfer", from_dept=DEP_ARC[1]), u0)
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid))

    async def _404(coro: Any, codigo: str) -> None:
        with pytest.raises(HTTPException) as exc:
            await coro
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == codigo

    await _404(svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=colega), "no_active_step")
    await _404(
        svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), colega), "no_active_step"
    )
    await _404(
        svc.devolver(rid, to_dept=DEP_ARC[1], causal="x", comentario=None, actor=colega), "no_active_step"
    )
    await _404(svc.complete_step(paso.id, FlowStepComplete(), colega), "step_not_found")
    with pytest.raises(RollbackError) as exc_rb:
        await svc.rollback(rid, colega, motivo="Intento de colega")
    assert exc_rb.value.code == "no_active_step"

    assert (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid)) == antes

    # Control positivo: el poseedor SÍ puede (la negativa de arriba no es un fallo genérico).
    await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u1)
    assert await _estado(conn, rid) == "closed"


# ---------------------------------------------------------------------------
# Los GET nunca cambian `radicados.status` (con deriva sembrada a propósito)
# ---------------------------------------------------------------------------


async def test_http_los_get_nunca_cambian_el_estado_aunque_haya_deriva(http: Any) -> None:
    c, conn, slug = http
    user = await _user(conn, DEP_JUR[0])
    # Radicados cuyo estado guardado NO es el que derivaría la función: si algún GET
    # sincronizara, cambiaría alguno de ellos.
    deriva = {
        "registered_con_pendiente": ("registered", ["pending"]),  # derivaría distributed
        "distributed_en_progreso": ("distributed", ["in_progress"]),  # derivaría in_progress
        "in_progress_sin_activo": ("in_progress", ["completed"]),  # se conserva
        "closed_con_activo": ("closed", ["pending"]),  # terminal
    }
    ids: dict[str, uuid.UUID] = {}
    for nombre, (estado, pasos) in deriva.items():
        rid = await _radicado(conn, estado)
        for n, st in enumerate(pasos, start=1):
            await _paso(conn, rid, n, st)
        ids[nombre] = rid
    await conn.execute("UPDATE flow_steps SET assigned_to = $1", user)
    foto = await conn.fetch("SELECT id, status, updated_at FROM radicados ORDER BY id")
    filas = (await _conteos(conn, slug), await _snapshot_pasos(conn))

    rutas = [
        "/api/v1/workflows/inbox",
        "/api/v1/workflows/pending",
        f"/api/v1/workflows/inbox?dept={DEP_JUR[1]}",
        f"/api/v1/workflows/pending?dept={DEP_JUR[1]}",
        "/api/v1/workflows/transaction-types",
    ]
    for rid in ids.values():
        rutas += [
            f"/api/v1/workflows/{rid}/current",
            f"/api/v1/workflows/{rid}/history",
            f"/api/v1/workflows/{rid}/events",
        ]
    devueltos = 0
    for ruta in rutas:
        r = await c.get(ruta, headers=_h(slug, user, canal="ui"))
        # `current` de un radicado sin paso activo es 404 neutro; el resto, 200.
        assert r.status_code in (200, 404), (ruta, r.text)
        if r.status_code == 200 and r.json():
            devueltos += 1
    assert devueltos >= 8, "las lecturas no devolvieron datos: la prueba sería trivialmente verde"

    assert await conn.fetch("SELECT id, status, updated_at FROM radicados ORDER BY id") == foto
    # Las lecturas pueden asentar traza de CONSULTA, pero no tocan pasos ni eventos de flujo.
    assert (await _conteos(conn, slug))[:2] == filas[0][:2]
    assert await _snapshot_pasos(conn) == filas[1]


# ---------------------------------------------------------------------------
# Cobertura por acto (lo que el carril mock apaga con `_sin_sincronizacion_de_estado`)
# ---------------------------------------------------------------------------


async def test_transfer_sincroniza_y_lo_deja_en_el_asiento(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    u2 = await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), u1)
    assert await _estado(conn, rid) == "in_progress"

    await svc.transfer(_assign(rid, DEP_ARC[1], u2, action="transfer", from_dept=DEP_JUR[1]), u1)

    # El paso nuevo nace `pending` (no leído por el nuevo responsable).
    assert await _estado(conn, rid) == "distributed"
    (asiento,) = await _asientos(conn, slug, "workflow.transfer")
    assert asiento["payload"]["estado_radicado"] == {"antes": "in_progress", "despues": "distributed"}


async def test_transaccion_anular_cancela_el_paso_y_sincroniza_sin_cerrar_el_radicado(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    await svc.execute_transaction(rid, TransactionRequest(tipo_tx="anular"), u1)

    # La transacción `anular` del catálogo cancela el PASO; `anulado` del radicado
    # lo escribe la anulación de document-service, no este acto.
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE radicado_id = $1", rid) == "cancelled"
    assert await _estado(conn, rid) == "distributed"
    (asiento,) = await _asientos(conn, slug, "workflow.transaccion")
    assert "estado_radicado" not in asiento["payload"]  # no cambió: sin clave


async def test_complete_de_un_paso_leido_conserva_in_progress(ctx: Any) -> None:
    """Fija el comportamiento actual de la función pura: sin paso activo, un
    `in_progress` se CONSERVA (solo `registered` con pasos pasa a `distributed`)."""
    svc, conn, _ = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    step = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), u1)

    await svc.complete_step(step.id, FlowStepComplete(), u1)

    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", step.id) == "completed"
    assert await _estado(conn, rid) == "in_progress"


# ---------------------------------------------------------------------------
# Migración 013: sobre un schema SIN `radicados` no falla
# ---------------------------------------------------------------------------


async def test_migracion_013_sobre_schema_sin_radicados_no_falla_y_es_idempotente(
    db_conn: asyncpg.Connection,
) -> None:
    """Tenant nuevo: el workflow-service migra antes de que el document-service cree
    `radicados`. La guarda debe saltar el backfill (NOTICE) sin error, y dejar la
    tabla de evidencia creada y vacía. Se corren TODAS las migraciones de tenant en
    un schema vacío, como lo haría el runner en un tenant recién aprovisionado."""
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    avisos: list[str] = []

    def _escucha(_conn: Any, msg: Any) -> None:
        avisos.append(msg.message)

    db_conn.add_log_listener(_escucha)
    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        archivos = list(runner._sql_files(runner.TENANT_DIR))
        sql_013 = next(p for p in archivos if "radicado_estado_desde_flujo" in p.name)
        for path in archivos:
            async with db_conn.transaction():
                await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await db_conn.execute(path.read_text())
        assert any("backfill de estado omitido" in a for a in avisos), avisos

        # Segunda corrida de 013 sobre el mismo schema vacío: tampoco falla.
        avisos.clear()
        async with db_conn.transaction():
            await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await db_conn.execute(sql_013.read_text())
        assert any("backfill de estado omitido" in a for a in avisos), avisos

        assert await db_conn.fetchval("SELECT to_regclass($1)", f'"{schema}".radicados') is None
        assert await db_conn.fetchval(f'SELECT count(*) FROM "{schema}".radicado_estado_recalculo') == 0
    finally:
        db_conn.remove_log_listener(_escucha)
        assert schema.startswith("tenant_itest_")
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


# ===========================================================================
# Remediación de auditoría (tenant-security-auditor + archival-compliance-auditor)
# ===========================================================================


async def _user_nivel(conn: asyncpg.Connection, nivel: int, dep: str | None = None) -> uuid.UUID:
    """Usuario cuyo clearance máximo es `nivel` (membresía + `role_clearance`)."""
    uid = await _user(conn, dep)
    grupo = uuid.uuid4()
    await conn.execute("INSERT INTO auth_memberships (user_id, group_id) VALUES ($1, $2)", uid, grupo)
    await conn.execute("INSERT INTO role_clearance (group_id, max_level) VALUES ($1, $2)", grupo, nivel)
    return uid


async def _radicado_nivel(conn: asyncpg.Connection, nivel: int) -> uuid.UUID:
    rid = await _radicado(conn)
    await conn.execute("UPDATE radicados SET nivel_seguridad = $2 WHERE id = $1", rid, nivel)
    return rid


async def _404(coro: Any, codigo: str) -> None:
    with pytest.raises(HTTPException) as exc:
        await coro
    assert (exc.value.status_code, exc.value.detail["error"]) == (404, codigo)


# --- 1. assign/transfer: tenencia y clearance --------------------------------


async def test_un_no_poseedor_no_puede_autoasignarse_un_radicado_con_paso_activo(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1, intruso = await _radicado(conn), await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid))

    await _404(svc.assign(_assign(rid, DEP_ARC[1], intruso), intruso), "no_active_step")
    await _404(
        svc.transfer(_assign(rid, DEP_ARC[1], intruso, action="transfer", from_dept=DEP_JUR[1]), intruso),
        "no_active_step",
    )

    assert (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid)) == antes
    # Y por tanto tampoco puede cerrarlo con «Finalizar»: no hay paso suyo.
    await _404(
        svc.finalizar(rid, comentario="Lo cierro yo", motivo_cierre="otro", actor=intruso), "no_active_step"
    )


async def test_el_poseedor_si_reparte_y_el_miembro_de_la_dependencia_reparte_un_paso_sin_dueno(ctx: Any) -> None:
    svc, conn, _ = ctx
    rid = await _radicado(conn)
    u1, u2, miembro = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    # Control positivo: el poseedor traslada.
    await svc.transfer(_assign(rid, DEP_ARC[1], None, action="transfer", from_dept=DEP_JUR[1]), u1)
    assert await _estado(conn, rid) == "distributed"
    # El paso quedó SIN dueño en ARC: cualquier miembro de ARC es poseedor y puede repartirlo.
    await svc.transfer(_assign(rid, DEP_JUR[1], u2, action="transfer", from_dept=DEP_ARC[1]), miembro)
    assert await conn.fetchval("SELECT assigned_to FROM flow_steps WHERE status = 'pending'") == u2


async def test_asignar_un_radicado_por_encima_del_clearance_da_el_mismo_404_que_si_no_existiera(ctx: Any) -> None:
    svc, conn, slug = ctx
    reservado = await _radicado_nivel(conn, 3)
    publico, alto = await _user_nivel(conn, 1), await _user_nivel(conn, 3)
    antes = await _conteos(conn, slug)

    await _404(svc.assign(_assign(reservado, DEP_JUR[1], None), publico), "radicado_not_found")
    inexistente = uuid.uuid4()
    await _404(svc.assign(_assign(inexistente, DEP_JUR[1], None), publico), "radicado_not_found")
    assert await _conteos(conn, slug) == antes  # ni paso, ni evento, ni asiento

    # Control positivo: con clearance suficiente sí.
    paso = await svc.assign(_assign(reservado, DEP_JUR[1], None), alto)
    assert paso.status == "pending"
    # `is_root` alcanza todo aunque no tenga membresías.
    root = await _user(conn)
    await conn.execute("UPDATE auth_users SET is_root = TRUE WHERE id = $1", root)
    otro = await _radicado_nivel(conn, 3)
    assert (await svc.assign(_assign(otro, DEP_JUR[1], None), root)).status == "pending"


async def test_sin_clearance_el_nivel_va_en_el_where_no_en_una_comprobacion_previa(ctx: Any) -> None:
    """La lectura del radicado lleva `nivel_seguridad <= $2`: lo que el actor no
    alcanza no se LEE (ni siquiera se resuelve su `tracking_number`)."""
    svc, conn, _ = ctx
    reservado = await _radicado_nivel(conn, 3)
    async with conn.transaction():
        assert await svc._repo.get_radicado_tracking_number(reservado, max_clearance=2) is None
        assert await svc._repo.get_radicado_tracking_number(reservado, max_clearance=3) is not None


async def test_el_reparto_automatico_de_sistema_no_se_rompe_con_un_radicado_reservado(ctx: Any) -> None:
    svc, conn, _ = ctx
    reservado = await _radicado_nivel(conn, 3)
    sistema = uuid.UUID("00000000-0000-0000-0000-000000000000")  # no existe en auth_users
    paso = await svc.assign(_assign(reservado, DEP_JUR[1], None), sistema, sistema=True)
    assert paso.status == "pending" and await _estado(conn, reservado) == "distributed"
    # Y sin `sistema=True`, ese mismo actor (sin clearance) NO puede.
    otro = await _radicado_nivel(conn, 3)
    await _404(svc.assign(_assign(otro, DEP_JUR[1], None), sistema), "radicado_not_found")


# --- 2. oráculo 409 y `anular` ---------------------------------------------------


async def test_un_no_poseedor_no_distingue_un_radicado_terminal_de_uno_ajeno(ctx: Any) -> None:
    """El 409 `radicado_terminal` solo lo ve quien posee el paso: el gate de
    tenencia va ANTES de sincronizar, también en `assign` y en `anular`."""
    svc, conn, _ = ctx
    rid, u1, intruso = await _radicado(conn), await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await conn.execute("UPDATE radicados SET status = 'anulado' WHERE id = $1", rid)  # zombi heredado

    await _404(svc.assign(_assign(rid, DEP_ARC[1], intruso), intruso), "no_active_step")
    await _404(
        svc.execute_transaction(rid, TransactionRequest(tipo_tx="anular"), intruso), "no_active_step"
    )
    # El poseedor sí lo ve (es quien tiene el paso): 409 terminal.
    with pytest.raises(HTTPException) as exc:
        await svc.execute_transaction(rid, TransactionRequest(tipo_tx="anular"), u1)
    assert exc.value.status_code == 409


async def test_una_transaccion_nueva_que_mueve_el_paso_exige_tenencia_aunque_su_fila_no_lo_declare(
    ctx: Any,
) -> None:
    svc, conn, _ = ctx
    await conn.execute(
        "INSERT INTO transaction_types (code, nombre, requires_permission, target_step_status) "
        "VALUES ('cierra_paso', 'Cierra el paso', 'PERM_TRAMITAR', 'completed')"
    )
    rid, u1, intruso = await _radicado(conn), await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    await _404(
        svc.execute_transaction(rid, TransactionRequest(tipo_tx="cierra_paso"), intruso), "no_active_step"
    )
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE radicado_id = $1", rid) == "pending"


# --- 4. complete_step: una sola definición de poseedor --------------------------


async def test_complete_step_no_cierra_el_paso_sin_dueno_de_otra_dependencia(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1, ajeno, miembro = (
        await _radicado(conn),
        await _user(conn, DEP_JUR[0]),
        await _user(conn, DEP_JUR[0]),
        await _user(conn, DEP_ARC[0]),
    )
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    paso = await svc.transfer(_assign(rid, DEP_ARC[1], None, action="transfer", from_dept=DEP_JUR[1]), u1)
    assert paso.assigned_to is None  # paso de dependencia SIN dueño
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn))

    # Antes del cambio: `assigned_to IS NULL` bastaba para que CUALQUIERA lo cerrara.
    await _404(svc.complete_step(paso.id, FlowStepComplete(), ajeno), "step_not_found")
    assert (await _conteos(conn, slug), await _snapshot_pasos(conn)) == antes

    cerrado = await svc.complete_step(paso.id, FlowStepComplete(), miembro)  # control positivo
    assert cerrado.status == "completed"


async def test_complete_step_no_recierra_un_paso_ya_cerrado(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    paso = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.complete_step(paso.id, FlowStepComplete(notes="uno"), u1)
    antes = await _conteos(conn, slug)

    await _404(svc.complete_step(paso.id, FlowStepComplete(notes="dos"), u1), "step_not_found")
    assert await _conteos(conn, slug) == antes


# --- 6. cascada: un solo cerrojo de alcance ----------------------------------------


async def test_la_cascada_no_toma_un_cerrojo_advisory_por_radicado(ctx: Any) -> None:
    """Agotaba `max_locks_per_transaction` con un barrido grande: ahora retiene UNO
    (el de alcance) sin importar cuántos radicados mueva."""
    svc, conn, _ = ctx
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    for _ in range(40):
        await svc.assign(_assign(await _radicado(conn), DEP_JUR[1], u1), u1)

    vistos: list[int] = []
    real = __import__("orpycamcp_common").audit.append

    async def _espia(c: Any, **kw: Any) -> Any:
        vistos.append(
            await c.fetchval(
                "SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid() AND locktype = 'advisory'"
            )
        )
        return await real(c, **kw)

    with patch("app.services.workflow_service.audit.append", new=_espia):
        res = await svc.reassign_cascade(
            from_assigned_to=u1, from_dept=None, to_assigned_to=u2, actor=u1
        )

    assert res.reassigned == 40
    assert vistos and max(vistos) <= 2, vistos  # 1 de alcance (+ el de la cadena de auditoría)


async def test_la_cascada_espera_a_los_actos_en_curso_y_los_actos_esperan_a_la_cascada(
    ctx: Any, nueva_conexion: Conexiones
) -> None:
    svc, conn, slug = ctx
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    rid = await _radicado(conn)
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    otra = await nueva_conexion()
    await otra.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    otro_svc = _ServicioConRadicador(otra, slug)
    tx = otra.transaction()
    await tx.start()
    await otro_svc._repo.lock_radicado(rid)  # un acto en curso (cerrojo de alcance COMPARTIDO)

    tarea = asyncio.create_task(
        svc.reassign_cascade(from_assigned_to=u1, from_dept=None, to_assigned_to=u2, actor=u1)
    )
    await asyncio.sleep(0.6)
    assert not tarea.done(), "la cascada debe esperar a que termine el acto en curso"
    await tx.commit()
    res = await asyncio.wait_for(tarea, 10)
    assert res.reassigned == 1


async def test_cascada_relee_y_bloquea_en_orden_tambien_lo_que_aparece_tras_el_primer_cerrojo(ctx: Any) -> None:
    """Dos radicados terminales y dos vivos: la cascada mueve solo los vivos y
    cuenta los pasos omitidos (los terminales se leen con su fila bloqueada)."""
    svc, conn, _ = ctx
    u1, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    vivos = [await _radicado(conn), await _radicado(conn)]
    for rid in vivos:
        await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    for estado in ("closed", "anulado"):
        rid = await _radicado(conn, estado)
        await _paso(conn, rid, 1, "pending")
        await conn.execute("UPDATE flow_steps SET assigned_to = $2 WHERE radicado_id = $1", rid, u1)
    res = await svc.reassign_cascade(from_assigned_to=u1, from_dept=None, to_assigned_to=u2, actor=u1)
    assert (res.reassigned, res.skipped_terminal) == (2, 2)


# --- 7. defensa en profundidad: los zombis no estorban --------------------------------


async def test_bandeja_pending_y_vencidos_excluyen_los_pasos_de_radicados_terminales(ctx: Any) -> None:
    svc, conn, _ = ctx
    u1 = await _user(conn, DEP_JUR[0])
    vivo = await _radicado(conn)
    await svc.assign(_assign(vivo, DEP_JUR[1], u1, due_at=datetime(2020, 1, 1, tzinfo=UTC)), u1)
    zombis = []
    for estado in ("closed", "anulado"):
        rid = await _radicado(conn, estado)
        await _paso(conn, rid, 1, "pending")
        await conn.execute(
            "UPDATE flow_steps SET assigned_to = $2, due_at = '2020-01-01' WHERE radicado_id = $1", rid, u1
        )
        zombis.append(rid)

    pasos, total = await svc.inbox(box=None, dept=None, assigned_to=None, page=1, size=50, user_id=u1)
    assert {p.radicado_id for p in pasos} == {vivo} and total == 1
    pendientes, total_p = await svc.list_pending(dept=None, assigned_to=None, page=1, size=50, user_id=u1)
    assert {p.radicado_id for p in pendientes} == {vivo} and total_p == 1
    vencidos = await svc._repo.list_overdue_unnotified()
    assert {p.radicado_id for p in vencidos} == {vivo}


# --- 7. migración 015: saneamiento de zombis ------------------------------------------


def _sql_de(nombre: str) -> str:
    return next(p for p in runner._sql_files(runner.TENANT_DIR) if nombre in p.name).read_text()


async def _correr_sql(conn: asyncpg.Connection, slug: str, nombre: str) -> None:
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{tenant_schema(slug)}", public')
        await conn.execute(_sql_de(nombre))


async def test_migracion_015_sanea_zombis_con_evidencia_inmutable_y_es_idempotente(ctx: Any) -> None:
    _, conn, slug = ctx
    anulado, cerrado, vivo = await _radicado(conn, "anulado"), await _radicado(conn, "closed"), await _radicado(conn)
    await _paso(conn, anulado, 1, "in_progress")
    await _paso(conn, cerrado, 1, "pending")
    await _paso(conn, vivo, 1, "pending")

    await _correr_sql(conn, slug, "sanear_pasos_zombi")

    estados = {
        r["radicado_id"]: r["status"] for r in await conn.fetch("SELECT radicado_id, status FROM flow_steps")
    }
    assert estados == {anulado: "cancelled", cerrado: "completed", vivo: "pending"}
    ev = await conn.fetch("SELECT * FROM flow_step_saneamiento ORDER BY estado_radicado")
    assert [(e["estado_radicado"], e["paso_antes"], e["paso_despues"], e["origen"]) for e in ev] == [
        ("anulado", "in_progress", "cancelled", "migracion_015"),
        ("closed", "pending", "completed", "migracion_015"),
    ]
    # Cada saneamiento dejó su evento operativo append-only, enlazado por id.
    for e in ev:
        assert await conn.fetchval(
            "SELECT tipo_tx FROM flow_events WHERE id = $1", e["flow_event_id"]
        ) == "saneamiento_terminal"
    # La evidencia es de SOLO inserción.
    for sql in ("UPDATE flow_step_saneamiento SET paso_despues = 'x'", "DELETE FROM flow_step_saneamiento"):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute(sql)

    # Idempotente: sin zombis, la 2.ª corrida no cambia nada ni añade evidencia.
    n_pasos, n_ev = await _snapshot_pasos(conn), await conn.fetchval("SELECT count(*) FROM flow_step_saneamiento")
    await _correr_sql(conn, slug, "sanear_pasos_zombi")
    assert await _snapshot_pasos(conn) == n_pasos
    assert await conn.fetchval("SELECT count(*) FROM flow_step_saneamiento") == n_ev == 2
    # Y el radicado vivo no se tocó.
    assert await _estado(conn, vivo) == "registered"


# --- 9. backfill 013: evidencia inmutable, sin tocar updated_at, ancla en audit_log ---


async def test_backfill_013_deja_evidencia_inmutable_no_toca_updated_at_y_ancla_en_audit_log(ctx: Any) -> None:
    _, conn, slug = ctx
    a, b, c = await _radicado(conn), await _radicado(conn), await _radicado(conn, "closed")
    await _paso(conn, a, 1, "pending")
    await _paso(conn, b, 1, "in_progress")
    await _paso(conn, c, 1, "pending")
    antes_fechas = {r["id"]: r["updated_at"] for r in await conn.fetch("SELECT id, updated_at FROM radicados")}

    await _correr_sql(conn, slug, "radicado_estado_desde_flujo")

    # `updated_at` NO se movió: recalcular un estado derivado no es editar el radicado.
    assert {r["id"]: r["updated_at"] for r in await conn.fetch("SELECT id, updated_at FROM radicados")} == antes_fechas
    ev = {e["radicado_id"]: e for e in await conn.fetch("SELECT * FROM radicado_estado_recalculo")}
    assert set(ev) == {a, b}  # el cerrado no se toca
    assert ev[a]["tracking_number"] == await _tracking(conn, a)
    assert (ev[a]["antes"], ev[a]["despues"], ev[a]["paso_activo"], ev[a]["origen"]) == (
        "registered", "distributed", "pending", "migracion_013",
    )
    assert (ev[b]["despues"], ev[b]["paso_activo"]) == ("in_progress", "in_progress")
    for sql in ("UPDATE radicado_estado_recalculo SET antes = 'x'", "DELETE FROM radicado_estado_recalculo"):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute(sql)

    # Asiento-ancla: lo escribe la APLICACIÓN (el runner), con conteo y SHA-256 reproducible.
    nombre = "013_radicado_estado_desde_flujo.sql"
    async with conn.transaction():
        assert await runner.anclar_evidencia(conn, tenant_schema(slug), nombre, tenant_slug=slug) is True
        # Una vez por tenant: la 2.ª llamada no escribe otro.
        assert await runner.anclar_evidencia(conn, tenant_schema(slug), nombre, tenant_slug=slug) is False
    (ancla,) = await _asientos(conn, slug, "workflow.estado_radicado.backfill")
    assert ancla["object_ref"] == "radicado_estado_recalculo"
    assert ancla["payload"]["filas"] == 2
    filas = await conn.fetch(
        "SELECT radicado_id, tracking_number, antes, despues, paso_activo, origen "
        "FROM radicado_estado_recalculo ORDER BY radicado_id, id"
    )
    contenido = "\n".join("|".join(str(v) for v in f.values()) for f in filas)
    assert ancla["payload"]["sha256"] == hashlib.sha256(contenido.encode()).hexdigest()
    # La cadena de hash sigue verificando (el asiento lo firmó la aplicación).
    from orpycamcp_common import audit as _audit

    assert (await _audit.verify_chain(conn, slug)).ok


async def test_el_runner_no_ancla_si_la_migracion_no_cambio_nada(ctx: Any) -> None:
    _, conn, slug = ctx
    async with conn.transaction():
        assert await runner.anclar_evidencia(
            conn, tenant_schema(slug), "013_radicado_estado_desde_flujo.sql", tenant_slug=slug
        ) is False
    assert await _asientos(conn, slug, "workflow.estado_radicado.backfill") == []


# --- 10/11/12. finalizar: motivo, evidencia en el asiento, sin paso activo ----------


async def test_finalizar_deja_motivo_y_evidencia_en_el_evento_y_en_el_asiento(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    paso = await svc.assign(_assign(rid, DEP_JUR[1], u1, due_at=datetime(2020, 1, 1, tzinfo=UTC)), u1)
    # Una salida que responde a este radicado, y vinculado a un expediente.
    await conn.execute(
        "INSERT INTO radicados (tracking_number, doc_type, responde_a) VALUES ('2026-ITEST-S-000001', 'S', $1)", rid
    )
    await conn.execute(
        "CREATE TABLE expediente_radicados (radicado_id UUID NOT NULL, estado VARCHAR(20) NOT NULL)"
    )
    await conn.execute("INSERT INTO expediente_radicados VALUES ($1, 'vinculado')", rid)

    await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u1)

    ev = await conn.fetchrow("SELECT id, detalles FROM flow_events WHERE tipo_tx = 'finalizar'")
    assert json.loads(ev["detalles"])["motivo_cierre"] == "respondido"
    (asiento,) = await _asientos(conn, slug, "workflow.finalizar")
    p = asiento["payload"]
    assert p["motivo_cierre"] == "respondido"
    assert p["step_id"] == str(paso.id) and p["flow_event_id"] == str(ev["id"])
    assert (p["tiene_respuesta"], p["en_expediente"], p["fuera_de_plazo"]) == (True, True, True)


async def test_finalizar_sin_respuesta_ni_expediente_ni_plazo_lo_dice_tal_cual(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.finalizar(rid, comentario="No requiere respuesta", motivo_cierre="nrr", actor=u1)
    (asiento,) = await _asientos(conn, slug, "workflow.finalizar")
    p = asiento["payload"]
    # `expediente_radicados` no existe en este tenant: «no consta» (None), no un falso `False`
    # y, sobre todo, no aborta la transacción del cierre.
    assert (p["tiene_respuesta"], p["en_expediente"], p["fuera_de_plazo"]) == (False, None, None)
    assert p["motivo_cierre"] == "nrr" and await _estado(conn, rid) == "closed"


async def test_finalizar_sin_paso_activo_lo_permite_quien_poseia_el_ultimo_paso_completado(ctx: Any) -> None:
    svc, conn, slug = ctx
    rid = await _radicado(conn)
    u1, otro, colega = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0]), await _user(conn, DEP_JUR[0])
    paso = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.complete_step(paso.id, FlowStepComplete(), u1)  # completó y no repartió: sin paso activo
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid))

    # Otro usuario: el MISMO 404 neutro.
    await _404(svc.finalizar(rid, comentario="Lo cierro yo", motivo_cierre="otro", actor=otro), "no_active_step")
    # Un colega de la dependencia NO posee un paso que tenía dueño: 404.
    await _404(svc.finalizar(rid, comentario="Lo cierro yo", motivo_cierre="otro", actor=colega), "no_active_step")
    assert (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid)) == antes

    cerrado = await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=u1)
    assert cerrado.id == paso.id and cerrado.status == "completed"  # no inventa un paso
    assert await _estado(conn, rid) == "closed"
    assert await conn.fetchval("SELECT count(*) FROM flow_steps WHERE radicado_id = $1", rid) == 1
    # Y ya cerrado, no se puede finalizar otra vez (404 neutro, no 409).
    await _404(svc.finalizar(rid, comentario="Otra vez", motivo_cierre="otro", actor=u1), "no_active_step")


async def test_http_finalizar_exige_motivo_tipificado(http: Any) -> None:
    c, conn, slug = http
    svc = _ServicioConRadicador(conn, slug)
    rid, u1 = await _radicado(conn), await _user(conn, DEP_JUR[0])
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    url = f"/api/v1/workflows/{rid}/finalizar"
    for cuerpo in (
        {"comentario": "Trámite concluido"},
        {"comentario": "Trámite concluido", "motivo_cierre": "porque sí"},
    ):
        assert (await c.post(url, json=cuerpo, headers=_h(slug, u1))).status_code == 422
    assert await _estado(conn, rid) == "distributed"
    r = await c.post(
        url, json={"comentario": "Trámite concluido", "motivo_cierre": "traslado_competencia"}, headers=_h(slug, u1)
    )
    assert r.status_code == 200 and await _estado(conn, rid) == "closed"


async def test_el_runner_aplica_013_y_015_y_ancla_en_la_misma_transaccion(
    ctx: Any, tmp_path: Any
) -> None:
    """El CABLEADO del runner (no solo `anclar_evidencia` suelta): al aplicar 013 y
    015 sobre un tenant con datos, cada una deja su asiento-ancla en `audit_log`,
    atómico con la migración y registrado en el libro."""
    _, conn, slug = ctx
    schema = tenant_schema(slug)
    a, z = await _radicado(conn), await _radicado(conn, "anulado")
    await _paso(conn, a, 1, "pending")
    await _paso(conn, z, 1, "pending")
    for nombre in ("013_radicado_estado_desde_flujo.sql", "015_sanear_pasos_zombi.sql"):
        (tmp_path / nombre).write_text((runner.TENANT_DIR / nombre).read_text())

    async def _schemas(_c: Any) -> list[str]:
        return [schema]

    try:
        with (
            patch.object(runner, "TENANT_DIR", tmp_path),
            patch.object(runner, "_tenant_schemas", new=_schemas),
        ):
            await runner._apply_tenant(conn)
        await conn.execute(f'SET search_path TO "{schema}", public')  # el runner lo deja en `public`
        slug_audit = schema.removeprefix("tenant_")  # sin fila en `public.tenants`: sufijo del schema
        acciones = {
            r["action"]: json.loads(r["payload"])
            for r in await conn.fetch(
                "SELECT action, payload FROM public.audit_log WHERE tenant_slug = $1", slug_audit
            )
        }
        assert acciones["workflow.estado_radicado.backfill"]["filas"] == 1
        assert acciones["workflow.flow_step.saneamiento"]["filas"] == 1
        assert await conn.fetchval("SELECT status FROM flow_steps WHERE radicado_id = $1", z) == "cancelled"
    finally:
        await conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)


# ---------------------------------------------------------------------------
# Re-auditoría de seguridad: residuales A-E y RN-8
# ---------------------------------------------------------------------------


async def _reservado_repartido_a_la_dependencia(svc: Any, conn: asyncpg.Connection) -> tuple[Any, ...]:
    """Reservado (nivel 3) repartido, sin dueño, a la dependencia JUR por quien lo
    radicó (nivel 3). `intruso`: miembro de JUR con clearance 1 (no lo alcanza)."""
    alto = await _user_nivel(conn, 3, DEP_JUR[0])
    intruso = await _user_nivel(conn, 1, DEP_JUR[0])
    rid = await _radicado_nivel(conn, 3)
    paso = await svc.assign(_assign(rid, DEP_JUR[1], None), alto)
    return rid, paso, alto, intruso


async def _intenta_todo(svc: Any, rid: uuid.UUID, paso_id: uuid.UUID, intruso: uuid.UUID) -> None:
    """Todos los actos por tenencia de `intruso` sobre `rid`: cada uno el 404 neutro."""
    await _404(
        svc.finalizar(rid, comentario="Lo cierro yo", motivo_cierre="otro", actor=intruso), "no_active_step"
    )
    await _404(svc.complete_step(paso_id, FlowStepComplete(), intruso), "step_not_found")
    await _404(svc.devolver(rid, to_dept=DEP_ARC[1], causal="x", comentario=None, actor=intruso), "no_active_step")
    with pytest.raises(RollbackError) as exc:
        await svc.rollback(rid, intruso, motivo="Intento de reversión")
    assert exc.value.code == "no_active_step"
    # `marcar_leido` exige tenencia: el gate de nivel ya corta en la lectura del paso.
    with pytest.raises(TransactionError) as exc2:
        await svc.execute_transaction(rid, TransactionRequest(tipo_tx="marcar_leido"), intruso)
    assert exc2.value.code == "no_flow"


async def test_a_un_miembro_de_clearance_1_no_actua_sobre_un_reservado_repartido_a_su_dependencia(ctx: Any) -> None:
    """[A] Tener el paso (sin dueño, de MI dependencia) no basta: el nivel del
    radicado va en el `WHERE` de cada lectura/escritura por tenencia."""
    svc, conn, slug = ctx
    rid, paso, alto, intruso = await _reservado_repartido_a_la_dependencia(svc, conn)
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid))

    await _intenta_todo(svc, rid, paso.id, intruso)

    assert (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid)) == antes
    # Control positivo: un miembro de la MISMA dependencia con clearance suficiente sí.
    colega = await _user_nivel(conn, 3, DEP_JUR[0])
    cerrado = await svc.finalizar(rid, comentario="Trámite concluido", motivo_cierre="respondido", actor=colega)
    assert cerrado.status == "completed" and await _estado(conn, rid) == "closed"


async def test_a_el_asignado_cuyo_nivel_ya_no_alcanza_el_radicado_tampoco_actua(ctx: Any) -> None:
    """[A] Reclasificación posterior al reparto: el poseedor por `assigned_to` pierde
    el acceso al subir el nivel del radicado (write-up por reclasificación)."""
    svc, conn, slug = ctx
    u1 = await _user_nivel(conn, 1, DEP_JUR[0])
    rid = await _radicado(conn)
    paso = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await conn.execute("UPDATE radicados SET nivel_seguridad = 3 WHERE id = $1", rid)
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn))

    await _intenta_todo(svc, rid, paso.id, u1)

    assert (await _conteos(conn, slug), await _snapshot_pasos(conn)) == antes


async def test_a_finalizar_sin_paso_activo_tambien_mira_el_nivel_del_poseedor(ctx: Any) -> None:
    """[A] La rama `ultimo_paso_si_lo_poseia` (último paso completado) también."""
    svc, conn, _ = ctx
    alto = await _user_nivel(conn, 3, DEP_JUR[0])
    intruso = await _user_nivel(conn, 1, DEP_JUR[0])
    rid = await _radicado_nivel(conn, 3)
    paso = await svc.assign(_assign(rid, DEP_JUR[1], None), alto)
    await conn.execute("UPDATE flow_steps SET status = 'completed' WHERE id = $1", paso.id)  # sin activo

    await _404(
        svc.finalizar(rid, comentario="Lo cierro yo", motivo_cierre="otro", actor=intruso), "no_active_step"
    )
    assert await _estado(conn, rid) != "closed"
    assert (
        await svc.finalizar(rid, comentario="Lo cierro yo", motivo_cierre="otro", actor=alto)
    ).status == "completed"


# --- B. radicado sin paso activo ------------------------------------------------


async def test_b_sin_ningun_paso_solo_reparte_quien_lo_radico(ctx: Any) -> None:
    svc, conn, slug = ctx
    base = WorkflowService(conn, slug)  # SIN el arnés: `registered_by` es el que es
    radicador, intruso = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    rid = await _radicado(conn)
    await conn.execute("UPDATE radicados SET registered_by = $2 WHERE id = $1", rid, radicador)
    antes = await _conteos(conn, slug)

    await _404(base.assign(_assign(rid, DEP_ARC[1], intruso), intruso), "no_active_step")
    await _404(
        base.transfer(_assign(rid, DEP_ARC[1], intruso, action="transfer", from_dept=DEP_JUR[1]), intruso),
        "no_active_step",
    )
    assert await _conteos(conn, slug) == antes and await conn.fetchval("SELECT count(*) FROM flow_steps") == 0

    paso = await base.assign(_assign(rid, DEP_JUR[1], None), radicador)  # el radicador sí
    assert paso.status == "pending"


async def test_b_un_radicado_sin_radicador_registrado_no_lo_reparte_nadie_a_mano(ctx: Any) -> None:
    """`registered_by` NULL (importado, ingestado por sistema): fail-closed salvo el
    reparto automático (`sistema=True`, que no pasa por esta regla)."""
    svc, conn, slug = ctx
    base = WorkflowService(conn, slug)
    u1 = await _user(conn, DEP_JUR[0])
    rid = await _radicado(conn)  # registered_by NULL

    await _404(base.assign(_assign(rid, DEP_JUR[1], u1), u1), "no_active_step")
    sistema = uuid.UUID("00000000-0000-0000-0000-000000000000")
    assert (await base.assign(_assign(rid, DEP_JUR[1], None), sistema, sistema=True)).status == "pending"


async def test_b_con_historial_y_sin_paso_activo_solo_reparte_quien_poseia_el_ultimo(ctx: Any) -> None:
    svc, conn, slug = ctx
    base = WorkflowService(conn, slug)
    u1, intruso = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    rid = await _radicado(conn)
    await conn.execute("UPDATE radicados SET registered_by = $2 WHERE id = $1", rid, u1)
    paso = await base.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await base.complete_step(paso.id, FlowStepComplete(), u1)  # todo completado, sin cerrar
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn))

    await _404(base.assign(_assign(rid, DEP_ARC[1], intruso), intruso), "no_active_step")
    assert (await _conteos(conn, slug), await _snapshot_pasos(conn)) == antes

    nuevo = await base.assign(_assign(rid, DEP_ARC[1], None), u1)  # quien poseía el último, sí
    assert nuevo.status == "pending"


async def test_b_el_radicador_deja_de_serlo_en_cuanto_hay_historial(ctx: Any) -> None:
    """Quien radicó solo reparte el PRIMER paso; con historial manda la tenencia
    del último paso (si no, el radicador reabriría cualquier trámite ajeno)."""
    svc, conn, slug = ctx
    base = WorkflowService(conn, slug)
    radicador, u2 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    rid = await _radicado(conn)
    await conn.execute("UPDATE radicados SET registered_by = $2 WHERE id = $1", rid, radicador)
    paso = await base.assign(_assign(rid, DEP_ARC[1], u2), radicador)
    await base.complete_step(paso.id, FlowStepComplete(), u2)

    await _404(base.assign(_assign(rid, DEP_JUR[1], radicador), radicador), "no_active_step")


# --- C (+H). transacciones sin tenencia ----------------------------------------


async def test_c_las_transacciones_sin_tenencia_no_devuelven_un_reservado_y_el_404_es_uno_solo(ctx: Any) -> None:
    """[C] y [H]: informar/vobo/nrr/agendar... sobre un reservado ajeno daban el
    `tracking_number`/`to_dept`. Ahora el no-autorizado recibe SIEMPRE `no_flow`, igual
    que ante un radicado inexistente o sin pasos: no hay oráculo entre los tres."""
    svc, conn, slug = ctx
    rid, _paso_, alto, intruso = await _reservado_repartido_a_la_dependencia(svc, conn)
    sin_pasos = await _radicado_nivel(conn, 3)
    inexistente = uuid.uuid4()
    antes = await _conteos(conn, slug)

    for tipo in ("informar", "vobo", "nrr", "agendar", "marcar_leido"):
        codigos = set()
        for objetivo in (rid, sin_pasos, inexistente):
            with pytest.raises(TransactionError) as exc:
                await svc.execute_transaction(objetivo, TransactionRequest(tipo_tx=tipo), intruso)
            codigos.add(exc.value.code)
        assert codigos == {"no_flow"}, tipo
    assert await _conteos(conn, slug) == antes  # ni evento ni asiento

    # Control positivo: con clearance suficiente la transacción corre y devuelve su evento.
    ev = await svc.execute_transaction(rid, TransactionRequest(tipo_tx="informar"), alto)
    assert ev.tracking_number == await _tracking(conn, rid)


# --- D. TOCTOU en rollback / devolver --------------------------------------------


async def test_d_devolver_relee_el_paso_y_la_tenencia_tras_el_cerrojo(ctx: Any, nueva_conexion: Conexiones) -> None:
    """Otro acto mueve el paso MIENTRAS `devolver` espera el cerrojo del radicado:
    al obtenerlo debe ver el estado nuevo (antes leía el paso fuera de la transacción
    y devolvía el paso ya ajeno)."""
    svc, conn, slug = ctx
    u1, u3 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    rid = await _radicado(conn)
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    otra = await nueva_conexion()
    await otra.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    tx = otra.transaction()
    await tx.start()
    await WorkflowService(otra, slug)._repo.lock_radicado(rid)
    await otra.execute("UPDATE flow_steps SET assigned_to = $2 WHERE radicado_id = $1", rid, u3)

    tarea = asyncio.create_task(svc.devolver(rid, to_dept=DEP_ARC[1], causal="x", comentario=None, actor=u1))
    await asyncio.sleep(0.6)
    assert not tarea.done(), "devolver debe esperar el cerrojo del radicado"
    await tx.commit()
    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(tarea, 10)
    assert (exc.value.status_code, exc.value.detail["error"]) == (404, "no_active_step")
    assert await conn.fetchval("SELECT count(*) FROM flow_steps WHERE radicado_id = $1", rid) == 1
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE radicado_id = $1", rid) == "pending"


async def test_d_rollback_relee_el_paso_y_la_tenencia_tras_el_cerrojo(ctx: Any, nueva_conexion: Conexiones) -> None:
    svc, conn, slug = ctx
    rid, u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    u3 = await _user(conn, DEP_JUR[0])

    otra = await nueva_conexion()
    await otra.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    tx = otra.transaction()
    await tx.start()
    await WorkflowService(otra, slug)._repo.lock_radicado(rid)
    await otra.execute("UPDATE flow_steps SET assigned_to = $2 WHERE id = $1", paso.id, u3)

    tarea = asyncio.create_task(svc.rollback(rid, u2, motivo="Asignada por error"))
    await asyncio.sleep(0.6)
    assert not tarea.done(), "rollback debe esperar el cerrojo del radicado"
    await tx.commit()
    with pytest.raises(RollbackError) as exc:
        await asyncio.wait_for(tarea, 10)
    assert exc.value.code == "no_active_step"
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", paso.id) == "pending"
    assert await conn.fetchval("SELECT count(*) FROM flow_steps WHERE status = 'cancelled'") == 0


async def test_d_los_apply_repiten_la_condicion_de_tenencia(ctx: Any) -> None:
    """Aunque el servicio leyera mal, `apply_rollback`/`apply_devolucion` no tocan
    nada si el actor ya no es poseedor (devuelven `None`)."""
    svc, conn, slug = ctx
    rid, u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    intruso = await _user(conn, DEP_JUR[0])
    antes = await _snapshot_pasos(conn)
    previo = await svc._repo.get_previous_step(rid, paso.step_number)
    async with conn.transaction():
        assert await svc._repo.apply_devolucion(
            current=paso, to_dept=DEP_JUR[1], causal="x", comentario=None, actor=intruso, max_clearance=3
        ) is None
        assert await svc._repo.apply_rollback(
            current=paso, previous=previo, actor=intruso, comentario="m", max_clearance=3
        ) is None
    assert await _snapshot_pasos(conn) == antes


# --- E. una sola definición de poseedor ------------------------------------------


async def test_e_rollback_y_devolver_aceptan_al_miembro_de_la_dependencia_si_el_paso_no_tiene_dueno(ctx: Any) -> None:
    """Antes `assigned_to == actor`: el paso sin dueño (el que deja una devolución) no
    lo podía revertir ni devolver NADIE de su dependencia. Ahora `_holder_condition`."""
    svc, conn, _ = ctx
    u1, miembro, ajeno = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0]), await _user(conn, DEP_JUR[0])
    rid = await _radicado(conn)
    await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)
    await svc.transfer(_assign(rid, DEP_ARC[1], None, action="transfer", from_dept=DEP_JUR[1]), u1)  # sin dueño

    with pytest.raises(RollbackError):
        await svc.rollback(rid, ajeno, motivo="No es mi dependencia")  # no pertenece a ARC
    await _404(svc.devolver(rid, to_dept=DEP_JUR[1], causal="x", comentario=None, actor=ajeno), "no_active_step")

    revertido = await svc.rollback(rid, miembro, motivo="Reparto por error")  # el miembro sí
    assert revertido.to_dept == DEP_JUR[1]
    await svc.transfer(_assign(rid, DEP_ARC[1], None, action="transfer", from_dept=DEP_JUR[1]), u1)
    devuelto = await svc.devolver(rid, to_dept=DEP_JUR[1], causal="DEV-DEP", comentario=None, actor=miembro)
    assert devuelto.to_dept == DEP_JUR[1]


# --- RN-8. la guarda va dentro del cerrojo ---------------------------------------


async def test_rn8_dos_repartos_de_sistema_concurrentes_dejan_un_solo_paso(ctx: Any, nueva_conexion: Conexiones) -> None:
    svc, conn, slug = ctx
    rid = await _radicado(conn)
    sistema = uuid.UUID("00000000-0000-0000-0000-000000000000")

    otra = await nueva_conexion()
    await otra.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    otro_svc = WorkflowService(otra, slug)
    tx = otra.transaction()
    await tx.start()
    await otro_svc._repo.lock_radicado(rid)  # la OTRA entrega, a medio camino
    tarea = asyncio.create_task(svc.assign(_assign(rid, DEP_JUR[1], None), sistema, sistema=True))
    await asyncio.sleep(0.6)
    assert not tarea.done()
    await otra.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, assigned_by, action) "
        "SELECT id, tracking_number, 1, $2, $3, 'assign' FROM radicados WHERE id = $1",
        rid,
        DEP_ARC[1],
        sistema,
    )
    await tx.commit()
    with pytest.raises(YaDistribuidoError):
        await asyncio.wait_for(tarea, 10)
    assert await conn.fetchval("SELECT count(*) FROM flow_steps WHERE radicado_id = $1", rid) == 1


# --- D (reforzado por mutación) ---------------------------------------------------
# Las defensas de [D] son DOS capas (relectura tras el cerrojo + `UPDATE` que repite
# la condición): una mutación sobre una sola no cae si la prueba solo mira el
# sentido «pierdo la tenencia» (la otra capa también lo corta). Cada capa se fija
# con el caso que SOLO ella distingue.


async def test_d_rollback_ve_la_tenencia_que_otro_acto_le_da_mientras_espera_el_cerrojo(
    ctx: Any, nueva_conexion: Conexiones
) -> None:
    """Sentido inverso al de la pérdida: quien NO era poseedor al llegar lo es al
    obtener el cerrojo. Una lectura previa al cerrojo (o fuera de la transacción) lo
    rechazaría con el estado viejo; la relectura posterior lo acepta. El `UPDATE`
    no puede suplir esto (solo corta, no concede)."""
    svc, conn, slug = ctx
    rid, u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    u3 = await _user(conn, DEP_JUR[0])  # no pertenece a ARC ni es el asignado

    otra = await nueva_conexion()
    await otra.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    tx = otra.transaction()
    await tx.start()
    await WorkflowService(otra, slug)._repo.lock_radicado(rid)
    await otra.execute("UPDATE flow_steps SET assigned_to = $2 WHERE id = $1", paso.id, u3)

    tarea = asyncio.create_task(svc.rollback(rid, u3, motivo="Me lo acaban de asignar"))
    await asyncio.sleep(0.6)
    assert not tarea.done(), "rollback debe esperar el cerrojo del radicado"
    await tx.commit()
    revertido = await asyncio.wait_for(tarea, 10)
    assert revertido.to_dept == DEP_JUR[1]
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", paso.id) == "cancelled"


async def test_d_devolver_ve_la_tenencia_que_otro_acto_le_da_mientras_espera_el_cerrojo(
    ctx: Any, nueva_conexion: Conexiones
) -> None:
    svc, conn, slug = ctx
    u1, u3 = await _user(conn, DEP_JUR[0]), await _user(conn, DEP_ARC[0])
    rid = await _radicado(conn)
    paso = await svc.assign(_assign(rid, DEP_JUR[1], u1), u1)

    otra = await nueva_conexion()
    await otra.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    tx = otra.transaction()
    await tx.start()
    await WorkflowService(otra, slug)._repo.lock_radicado(rid)
    await otra.execute("UPDATE flow_steps SET assigned_to = $2 WHERE id = $1", paso.id, u3)

    tarea = asyncio.create_task(svc.devolver(rid, to_dept=DEP_JUR[1], causal="x", comentario=None, actor=u3))
    await asyncio.sleep(0.6)
    assert not tarea.done(), "devolver debe esperar el cerrojo del radicado"
    await tx.commit()
    nuevo = await asyncio.wait_for(tarea, 10)
    assert nuevo.to_dept == DEP_JUR[1] and nuevo.step_number == 2
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", paso.id) == "returned"


async def test_d_los_apply_tambien_exigen_el_nivel_del_radicado_en_el_update(ctx: Any) -> None:
    """El poseedor legítimo (asignado, paso vigente) pero con el radicado ya por encima
    de su clearance: ni `apply_rollback` ni `apply_devolucion` tocan nada. El control
    positivo con el nivel suficiente prueba que la negativa es por el nivel y no por
    otra condición."""
    svc, conn, _ = ctx
    rid, u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    previo = await svc._repo.get_previous_step(rid, paso.step_number)
    await conn.execute("UPDATE radicados SET nivel_seguridad = 3 WHERE id = $1", rid)
    antes = await _snapshot_pasos(conn)
    async with conn.transaction():
        assert await svc._repo.apply_rollback(
            current=paso, previous=previo, actor=u2, comentario="m", max_clearance=1
        ) is None
        assert await svc._repo.apply_devolucion(
            current=paso, to_dept=DEP_JUR[1], causal="x", comentario=None, actor=u2, max_clearance=1
        ) is None
    assert await _snapshot_pasos(conn) == antes
    async with conn.transaction():  # control positivo: mismo actor, nivel suficiente
        assert await svc._repo.apply_rollback(
            current=paso, previous=previo, actor=u2, comentario="m", max_clearance=3
        ) is not None


async def test_d_los_apply_tambien_exigen_el_estado_del_paso_en_el_update(ctx: Any) -> None:
    """`current` puede venir viejo: si el paso ya no está activo (completado) o, para
    `rollback`, ya no está `pending` (lo leyeron: `in_progress`), el `UPDATE` no casa y
    no se cancela/devuelve un paso que ya terminó."""
    svc, conn, _ = ctx
    rid, u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    previo = await svc._repo.get_previous_step(rid, paso.step_number)

    await conn.execute("UPDATE flow_steps SET status = 'in_progress' WHERE id = $1", paso.id)
    async with conn.transaction():
        assert await svc._repo.apply_rollback(
            current=paso, previous=previo, actor=u2, comentario="m", max_clearance=3
        ) is None  # rollback exige `pending`

    await conn.execute("UPDATE flow_steps SET status = 'completed' WHERE id = $1", paso.id)
    antes = await _snapshot_pasos(conn)
    async with conn.transaction():
        assert await svc._repo.apply_rollback(
            current=paso, previous=previo, actor=u2, comentario="m", max_clearance=3
        ) is None
        assert await svc._repo.apply_devolucion(
            current=paso, to_dept=DEP_JUR[1], causal="x", comentario=None, actor=u2, max_clearance=3
        ) is None
    assert await _snapshot_pasos(conn) == antes

    await conn.execute("UPDATE flow_steps SET status = 'in_progress' WHERE id = $1", paso.id)
    async with conn.transaction():  # control positivo: `devolver` sí admite in_progress
        assert await svc._repo.apply_devolucion(
            current=paso, to_dept=DEP_JUR[1], causal="x", comentario=None, actor=u2, max_clearance=3
        ) is not None


async def test_d_si_el_update_no_escribio_el_servicio_responde_404_neutro_y_no_asienta(ctx: Any) -> None:
    """Si `apply_*` devuelve `None` (la carrera que el `UPDATE` corta), el servicio no
    sincroniza, no asienta y no emite: el mismo 404 neutro, sin 500 por `None`."""
    svc, conn, slug = ctx
    rid, u1, u2, paso = await _circuito_dos_pasos(svc, conn)
    antes = (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid))

    with patch.object(svc._repo, "apply_rollback", new=AsyncMock(return_value=None)):
        with pytest.raises(RollbackError) as exc:
            await svc.rollback(rid, u2, motivo="x")
    assert exc.value.code == "no_active_step"
    with patch.object(svc._repo, "apply_devolucion", new=AsyncMock(return_value=None)):
        await _404(
            svc.devolver(rid, to_dept=DEP_JUR[1], causal="x", comentario=None, actor=u2), "no_active_step"
        )
    assert (await _conteos(conn, slug), await _snapshot_pasos(conn), await _estado(conn, rid)) == antes


# --- B (reforzado por mutación) ----------------------------------------------------


async def test_b_el_radicador_tampoco_reparte_lo_que_esta_por_encima_de_su_clearance(ctx: Any) -> None:
    """El nivel va en el `WHERE` de la rama «sin ningún paso» (`registered_by`) de
    `puede_repartir_sin_paso_activo`. A través del servicio lo corta antes la lectura
    del radicado (`get_radicado_tracking_number`, también con nivel); por eso se fija
    aquí, en la propia regla: si esa lectura previa cambiara, esta no queda abierta."""
    svc, conn, _ = ctx
    radicador = await _user(conn, DEP_JUR[0])
    rid = await _radicado(conn)
    await conn.execute(
        "UPDATE radicados SET registered_by = $2, nivel_seguridad = 3 WHERE id = $1", rid, radicador
    )
    repo = svc._repo
    assert await repo.puede_repartir_sin_paso_activo(rid, radicador, max_clearance=1) is False
    assert await repo.puede_repartir_sin_paso_activo(rid, radicador, max_clearance=3) is True  # control
    assert await repo.puede_repartir_sin_paso_activo(rid, None, max_clearance=3) is False  # sin actor
