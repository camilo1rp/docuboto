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
`ubicacion_*` sale del `COALESCE` por pares; y que el backfill SQL coincide fila a
fila con la función pura `derivar_ubicacion`.

Carril de integración (`RUN_INTEGRATION=1`, ver `conftest.py`).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.services.estado_radicado import PasoUbicacion, Ubicacion, derivar_ubicacion
from migrations import runner

# Los fixtures y ayudas del carril de ADR-028 (mismo arnés: RBAC concedido, Redis
# mock, radicador del reparto). Importarlos es la forma de compartir fixtures
# entre módulos sin moverlos a un conftest que los aplicaría a todo el carril.
from tests.integration.test_estado_radicado_integration import (  # noqa: F401
    DEP_ARC,
    DEP_JUR,
    _asientos,
    _assign,
    _correr_sql,
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
        await conn.execute(
            "INSERT INTO dependencias (codigo, nombre, activo) VALUES ($1, $2, $3)",
            codigo, nombre, activo,
        )


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
    from app.schemas.workflow import FlowStepComplete

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
        patch("app.services.workflow_service.audit.append", new=AsyncMock(side_effect=RuntimeError("audit caído"))),
        pytest.raises(RuntimeError, match="audit caído"),
    ):
        await svc.assign(_assign(rid, DEP_JUR[1], u), u)

    assert await _ubic(conn, rid) == antes
    assert antes["flujo_dept"] is None and antes["ubicacion_dept"] == TEC[1]


# ---------------------------------------------------------------------------
# Backfill 016: fila a fila contra la función pura, idempotente, con evidencia
# ---------------------------------------------------------------------------


_AUTO: Any = object()


async def _paso_en(
    conn: asyncpg.Connection, rid: uuid.UUID, n: int, status: str, dept: str,
    *, minutos: int | None = None, usuario: uuid.UUID | None = None, code: Any = _AUTO,
) -> None:
    """Un paso con su CÓDIGO (ADR-032). Por omisión, el de la dependencia ACTIVA de ese
    nombre; un paso vivo exige código (`ck_flow_steps_vivo_con_codigo`), uno cerrado puede
    llevarlo NULL (`code=None`: la historia que la 019 no pudo resolver)."""
    if code is _AUTO:
        code = await conn.fetchval(
            "SELECT min(codigo) FROM dependencias WHERE nombre = $1 AND activo", dept
        )
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, to_dept_code, "
        "assigned_to, assigned_by, action, status, completed_at) SELECT id, tracking_number, $2, $3, $7, $4, "
        "gen_random_uuid(), 'assign', $5, "
        "CASE WHEN $6::int IS NULL THEN NULL ELSE timestamptz '2026-10-01 08:00+00' + $6 * interval '1 minute' END "
        "FROM radicados WHERE id = $1",
        rid, n, dept, usuario, status, minutos, code,
    )


async def _casos_de_backfill(conn: asyncpg.Connection) -> list[uuid.UUID]:
    await _dependencias(
        conn, (*TEC, True), (*TH, True), (*CI, True),
        (610, "Duplicada", True), (611, "Duplicada", True),
        (620, "Solo", False), (621, "Solo", True), (630, "Cerrada", False),
    )
    u = await _user(conn)
    ids: list[uuid.UUID] = []

    async def caso(status: str, pasos: list[tuple], **previa: Any) -> None:
        rid = await _radicado_en(conn, TEC, status)
        for n, st, dept, min_, *code in pasos:
            await _paso_en(conn, rid, n, st, dept, minutos=min_, usuario=u, **({"code": code[0]} if code else {}))
        if previa:
            await conn.execute(
                "UPDATE radicados SET flujo_dept = $2, flujo_dept_code = $3 WHERE id = $1",
                rid, previa["dept"], previa["code"],
            )
        ids.append(rid)

    await caso("registered", [])
    await caso("distributed", [(1, "pending", TH[1], None)])
    await caso("in_progress", [(1, "returned", TH[1], 5), (2, "pending", CI[1], None)])
    await caso("distributed", [(1, "completed", TH[1], 30), (2, "cancelled", CI[1], 10)])  # rollback + completar
    await caso("distributed", [(1, "completed", TH[1], 5), (2, "completed", CI[1], 5)])  # empate
    # ADR-032: el código es un DATO del paso, no se adivina por el nombre.
    await caso("distributed", [(1, "pending", "Duplicada", None, 611)])  # homónimas: la del paso
    await caso("distributed", [(1, "pending", "Solo", None, 621)])
    await caso("distributed", [(1, "pending", "Renombrada", None, 300)])  # nombre que ya no existe
    await caso("distributed", [(1, "completed", "Cerrada", 4, None)])  # historia sin código: NULL
    await caso("closed", [(1, "completed", TH[1], 9)])  # cerrado anterior a ADR-029
    await caso("anulado", [(1, "cancelled", CI[1], 9)])  # anulado anterior a ADR-029
    await caso("closed", [(1, "completed", TH[1], 9)], dept="Antigua", code=1)  # ya escrito: se congela
    await caso("distributed", [], dept="Sobra", code=2)  # sin pasos pero con valor previo
    await caso("closed", [])
    return ids


async def _pasos_python(conn: asyncpg.Connection, rid: uuid.UUID) -> list[PasoUbicacion]:
    return [
        PasoUbicacion(
            step_number=p["step_number"], status=p["status"], to_dept=p["to_dept"],
            assigned_to=p["assigned_to"], completed_at=p["completed_at"],
            to_dept_code=p["to_dept_code"],
        )
        for p in await conn.fetch("SELECT * FROM flow_steps WHERE radicado_id = $1", rid)
    ]


async def test_backfill_coincide_fila_a_fila_con_la_funcion_pura_y_la_2a_corrida_no_cambia_nada(ctx: Any) -> None:
    _, conn, slug = ctx
    ids = await _casos_de_backfill(conn)
    previas: dict[uuid.UUID, Ubicacion | None] = {}
    estados: dict[uuid.UUID, str] = {}
    for rid in ids:
        r = await conn.fetchrow(
            "SELECT status, flujo_dept, flujo_dept_code, flujo_usuario FROM radicados WHERE id = $1", rid
        )
        estados[rid] = r["status"]
        previas[rid] = (
            Ubicacion(r["flujo_dept"], r["flujo_dept_code"], r["flujo_usuario"]) if r["flujo_dept"] else None
        )
    fechas = {r["id"]: r["updated_at"] for r in await conn.fetch("SELECT id, updated_at FROM radicados")}

    # Gemelo de `derivar_ubicacion` en SQL: la FASE C de la 019 (el código sale del PASO).
    await _correr_sql(conn, slug, "flujo_dependencia_por_codigo")

    cambiados = 0
    for rid in ids:
        esperada = derivar_ubicacion(estados[rid], await _pasos_python(conn, rid), congelada=previas[rid])
        r = await conn.fetchrow(
            "SELECT flujo_dept, flujo_dept_code, flujo_usuario FROM radicados WHERE id = $1", rid
        )
        real = Ubicacion(r["flujo_dept"], r["flujo_dept_code"], r["flujo_usuario"]) if r["flujo_dept"] else None
        assert real == esperada, (estados[rid], previas[rid])
        cambiados += real != previas[rid]
    # `updated_at` no se movió: recalcular un dato derivado no es editar el radicado.
    assert {r["id"]: r["updated_at"] for r in await conn.fetch("SELECT id, updated_at FROM radicados")} == fechas
    assert await conn.fetchval(
        "SELECT count(*) FROM radicado_ubicacion_recalculo WHERE origen = 'migracion_019'"
    ) == cambiados > 0

    # El cerrado ya escrito se congela (la evidencia no lo menciona).
    congelado = ids[11]
    assert (await _ubic(conn, congelado))["flujo_dept"] == "Antigua"
    assert congelado not in {e["radicado_id"] for e in await conn.fetch("SELECT radicado_id FROM radicado_ubicacion_recalculo")}

    # 2.ª corrida: 0 cambios y 0 filas de evidencia nuevas.
    antes = [await _ubic(conn, rid) for rid in ids]
    await _correr_sql(conn, slug, "flujo_dependencia_por_codigo")
    assert [await _ubic(conn, rid) for rid in ids] == antes
    assert await conn.fetchval("SELECT count(*) FROM radicado_ubicacion_recalculo") == cambiados


async def test_backfill_016_evidencia_inmutable_y_ancla_en_audit_log(ctx: Any) -> None:
    _, conn, slug = ctx
    rid = await _radicado_en(conn, None)
    await _paso_en(conn, rid, 1, "pending", DEP_JUR[1])
    await _correr_sql(conn, slug, "radicado_ubicacion_desde_flujo")

    (ev,) = await conn.fetch("SELECT * FROM radicado_ubicacion_recalculo")
    assert (ev["radicado_id"], ev["despues_dept"], ev["despues_dept_code"], ev["origen"]) == (
        rid, DEP_JUR[1], 100, "migracion_016",
    )
    assert (ev["antes_dept"], ev["estado_radicado"]) == (None, "registered")
    for sql in ("UPDATE radicado_ubicacion_recalculo SET antes_dept = 'x'", "DELETE FROM radicado_ubicacion_recalculo"):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute(sql)

    nombre = "016_radicado_ubicacion_desde_flujo.sql"
    async with conn.transaction():
        assert await runner.anclar_evidencia(conn, tenant_schema(slug), nombre, tenant_slug=slug) is True
        assert await runner.anclar_evidencia(conn, tenant_schema(slug), nombre, tenant_slug=slug) is False
    (ancla,) = await _asientos(conn, slug, "workflow.ubicacion_radicado.backfill")
    assert ancla["object_ref"] == "radicado_ubicacion_recalculo" and ancla["payload"]["filas"] == 1
    filas = await conn.fetch(
        "SELECT radicado_id, tracking_number, estado_radicado, antes_dept, antes_dept_code, antes_usuario, "
        "despues_dept, despues_dept_code, despues_usuario, paso_id, origen "
        "FROM radicado_ubicacion_recalculo ORDER BY radicado_id, id"
    )
    contenido = "\n".join("|".join(str(v) for v in f.values()) for f in filas)
    assert ancla["payload"]["sha256"] == hashlib.sha256(contenido.encode()).hexdigest()


async def test_016_guardas_de_orden_sin_radicados_omite_y_sin_columnas_falla(db_conn: asyncpg.Connection) -> None:
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    avisos: list[str] = []

    def _escucha(_c: Any, msg: Any) -> None:
        avisos.append(msg.message)

    sql = (runner.TENANT_DIR / "016_radicado_ubicacion_desde_flujo.sql").read_text()
    db_conn.add_log_listener(_escucha)
    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        async with db_conn.transaction():
            await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await db_conn.execute(sql)
        assert any("backfill de ubicación omitido" in a for a in avisos), avisos

        # `radicados` existe pero document-service aún no migró: NO se omite en silencio.
        async with db_conn.transaction():
            await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await db_conn.execute("CREATE TABLE radicados (id UUID PRIMARY KEY, status VARCHAR(20))")
        with pytest.raises(asyncpg.PostgresError, match="037"):
            async with db_conn.transaction():
                await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await db_conn.execute(sql)
    finally:
        db_conn.remove_log_listener(_escucha)
        assert schema.startswith("tenant_itest_")
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
