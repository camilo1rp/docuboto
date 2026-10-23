# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


# ruff: noqa: F811 — `ctx` y compañía son fixtures importados de otro módulo (arnés de ADR-028).
"""B5 (ADR-032) contra Postgres REAL: la dependencia se correlaciona por CÓDIGO.

Dos bloques:

* **La migración 019** sobre un schema que llega a la 018 con datos «de antes» (solo
  nombres): ambiguo/huérfano en trámite vivo FALLA CERRADO (y no queda ni una columna ni
  una fila de evidencia), el mapeo manual lo desbloquea, el historial se resuelve, las
  reglas y sus condiciones se reescriben, `flow_events` (append-only) solo recibe el código,
  es idempotente y deja ancla.
* **El comportamiento**: renombrar una dependencia con trámite vivo no saca el paso de la
  bandeja ni de la ubicación; el origen lo deriva el servidor; el alias por nombre no
  desempata homónimas.

Mutaciones verificadas: `_user_depts_subquery` vuelto a `d.nombre` -> cae
`test_renombrar_con_tramite_vivo_*`; `count(*) = 1` -> `min()` en la función (copia del
conftest) -> cae `test_backfill_ambiguo_*`; el RAISE por NOTICE -> cae el mismo; quitar el
re-habilitado del trigger -> cae `test_flow_events_*`.

Carril de integración (`RUN_INTEGRATION=1`, ver `conftest.py`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.services.estado_radicado import PasoUbicacion, Ubicacion, derivar_ubicacion
from migrations import runner
from tests.integration.conftest import _STUB_TABLES_DDL, _make_slug
from tests.integration.test_estado_radicado_integration import (  # noqa: F401
    _asientos,
    _assign,
    _radicado,
    _sin_permisos_ni_redis,
    _user,
    ctx,
)

pytestmark = pytest.mark.integration

_019 = "019_flujo_dependencia_por_codigo.sql"


def _sql_019() -> str:
    return (runner.TENANT_DIR / _019).read_text()


def _sql_020() -> str:
    return (runner.TENANT_DIR / "020_backfill_evidencia_completa.sql").read_text()


# ---------------------------------------------------------------------------
# Un schema que llega a la 018: datos anteriores a la 019 (solo nombres)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pre019(
    db_conn: asyncpg.Connection, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[str, str, asyncpg.Connection], None]:
    slug = _make_slug(request.node.name)
    schema = tenant_schema(slug)
    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        async with db_conn.transaction():
            await db_conn.execute(f'SET LOCAL search_path = "{schema}"')
            await db_conn.execute(_STUB_TABLES_DDL)
        for path in runner._sql_files(runner.TENANT_DIR):
            if path.name < "019":
                async with db_conn.transaction():
                    await db_conn.execute(f'SET LOCAL search_path = "{schema}"')
                    await db_conn.execute(path.read_text())
        await db_conn.execute(f'SET search_path TO "{schema}"')
        yield slug, schema, db_conn
    finally:
        assert schema.startswith("tenant_itest_")
        await db_conn.execute("RESET search_path")
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


async def _dep(conn: asyncpg.Connection, codigo: int, nombre: str, activo: bool = True) -> None:
    await conn.execute(
        "INSERT INTO dependencias (codigo, nombre, activo) VALUES ($1, $2, $3)", codigo, nombre, activo
    )


async def _step(
    conn: asyncpg.Connection, rid: uuid.UUID, n: int, status: str, to_dept: str,
    from_dept: str | None = None,
) -> uuid.UUID:
    """Un paso «de antes»: solo el NOMBRE (las columnas `*_code` todavía no existen)."""
    return await conn.fetchval(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, from_dept, to_dept, "
        "assigned_by, action, status, completed_at) SELECT id, tracking_number, $2, $5, $3, "
        "gen_random_uuid(), 'assign', $4::text, "
        "CASE WHEN $4::text IN ('completed','cancelled','returned') THEN now() END "
        "FROM radicados WHERE id = $1 RETURNING id",
        rid, n, to_dept, status, from_dept,
    )


async def _event(conn: asyncpg.Connection, rid: uuid.UUID, to_dept: str, from_dept: str | None = None) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO flow_events (radicado_id, tracking_number, tipo_tx, from_dept, to_dept) "
        "SELECT id, tracking_number, 'assign', $3, $2 FROM radicados WHERE id = $1 RETURNING id",
        rid, to_dept, from_dept,
    )


async def _rule(conn: asyncpg.Connection, assign: str, conditions: list | None = None, dest: str | None = None) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO workflow_rules (name, assign_to_dept, dest_dept, conditions, created_by) "
        "VALUES ('r', $1, $2, $3::jsonb, gen_random_uuid()) RETURNING id",
        assign, dest, json.dumps(conditions or []),
    )


async def _aplicar_019(conn: asyncpg.Connection) -> None:
    await conn.execute(_sql_019())


async def _sin_019(conn: asyncpg.Connection) -> None:
    """Tras un fallo la transacción entera se revirtió: ni columnas ni evidencia."""
    assert await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'flow_steps' AND column_name = 'to_dept_code'"
    ) == 0
    assert await conn.fetchval("SELECT to_regclass('dependencia_codigo_backfill')") is None


# ---------------------------------------------------------------------------
# Backfill: ambiguo/huérfano en trámite vivo FALLA CERRADO
# ---------------------------------------------------------------------------


async def test_backfill_ambiguo_en_paso_vivo_falla_y_no_deja_nada(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo", activo=False)
    rid = await _radicado(conn)
    await _step(conn, rid, 1, "pending", "Archivo")

    with pytest.raises(asyncpg.RaiseError, match="Archivo"):
        await _aplicar_019(conn)

    await _sin_019(conn)  # rollback completo: la migración NO queda a medias


async def test_el_mapeo_manual_desbloquea_el_backfill_y_queda_como_mapeado(pre019: Any) -> None:
    """Homónimas + paso vivo => RAISE; tras el mapeo el nombre resuelve a la elegida.

    Mutación: volver `resolver_codigo_dependencia` al UNION plano (el mapeo se suma a
    las coincidencias textuales) deja la 2.ª aplicación en RAISE: el mapeo no tendría
    efecto (el defecto que corrige la 014 de tenant-service).
    """
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo", activo=False)
    rid = await _radicado(conn)
    paso = await _step(conn, rid, 1, "pending", "Archivo")
    with pytest.raises(asyncpg.RaiseError):
        await _aplicar_019(conn)

    hid = await conn.fetchval(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
        "VALUES (901, 'Archivo', 'mapeo_manual') RETURNING id"
    )
    await _aplicar_019(conn)

    assert await conn.fetchval("SELECT to_dept_code FROM flow_steps WHERE id = $1", paso) == 901
    ev = await conn.fetchrow("SELECT * FROM dependencia_codigo_backfill WHERE fila_id = $1", paso)
    assert (ev["resultado"], ev["codigo"], ev["vivo"]) == ("mapeado", 901, True)
    # 020: el 'mapeado' queda ENLAZADO a la fila de mapeo que lo decidió.
    await conn.execute(_sql_020())
    assert await conn.fetchval("SELECT historico_id FROM dependencia_codigo_backfill WHERE fila_id = $1", paso) == hid


async def test_dos_mapeos_distintos_para_el_mismo_nombre_siguen_abortando(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo")
    rid = await _radicado(conn)
    await _step(conn, rid, 1, "pending", "Archivo")
    for cod in (901, 902):
        await conn.execute(
            "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES ($1, 'Archivo', 'mapeo_manual')",
            cod,
        )
    with pytest.raises(asyncpg.RaiseError, match="Archivo"):
        await _aplicar_019(conn)
    await _sin_019(conn)


async def test_backfill_resuelve_el_unico_candidato_y_un_nombre_anterior(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Gestión Documental")
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) VALUES (901, 'Archivo', 'renombre')"
    )
    rid = await _radicado(conn)
    viejo = await _step(conn, rid, 1, "pending", "Archivo")
    nuevo = await _step(conn, rid, 2, "pending", "Gestión Documental")
    await _aplicar_019(conn)
    for paso in (viejo, nuevo):
        assert await conn.fetchval("SELECT to_dept_code FROM flow_steps WHERE id = $1", paso) == 901
    # El nombre (instantánea) NO se tocó.
    assert await conn.fetchval("SELECT to_dept FROM flow_steps WHERE id = $1", viejo) == "Archivo"


async def test_huerfano_terminal_o_de_origen_queda_nulo_con_evidencia_y_el_vivo_aborta(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    rid = await _radicado(conn)
    cerrado = await _step(conn, rid, 1, "completed", "Inexistente", from_dept="Tampoco")
    vivo = await _step(conn, rid, 2, "pending", "Archivo", from_dept="Origen perdido")
    await _aplicar_019(conn)

    p = await conn.fetchrow("SELECT to_dept_code, from_dept_code FROM flow_steps WHERE id = $1", cerrado)
    assert (p["to_dept_code"], p["from_dept_code"]) == (None, None)
    assert await conn.fetchval("SELECT to_dept_code FROM flow_steps WHERE id = $1", vivo) == 901
    ev = {
        (r["fila_id"], r["columna"]): (r["resultado"], r["vivo"])
        for r in await conn.fetch("SELECT * FROM dependencia_codigo_backfill")
    }
    assert ev[(cerrado, "to_dept")] == ("huerfano", False)
    assert ev[(vivo, "from_dept")] == ("huerfano", True)  # el origen no gobierna tenencia: no aborta
    assert ev[(vivo, "to_dept")] == ("resuelto", True)


async def test_huerfano_en_paso_vivo_aborta(pre019: Any) -> None:
    _, _, conn = pre019
    rid = await _radicado(conn)
    await _step(conn, rid, 1, "in_progress", "Inexistente")
    with pytest.raises(asyncpg.RaiseError, match="Inexistente"):
        await _aplicar_019(conn)
    await _sin_019(conn)


# ---------------------------------------------------------------------------
# Reglas y condiciones
# ---------------------------------------------------------------------------


async def test_regla_con_destino_ambiguo_aborta(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo")
    await _rule(conn, "Archivo")
    with pytest.raises(asyncpg.RaiseError, match="Archivo"):
        await _aplicar_019(conn)
    await _sin_019(conn)


async def test_condiciones_por_nombre_se_reescriben_a_codigo_y_las_textuales_se_conservan(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Jurídica")
    rid = await _rule(
        conn,
        "Archivo",
        [
            {"field": "dest_dept", "op": "eq", "value": "Archivo"},
            {"field": "origin_dept", "op": "in", "value": ["Archivo", "Jurídica"]},
            {"field": "dest_dept", "op": "ne", "value": "Jurídica"},
            {"field": "subject", "op": "contains", "value": "Archivo"},
            {"field": "dest_dept", "op": "regex", "value": "^Arch"},
        ],
        dest="Jurídica",
    )
    await _aplicar_019(conn)

    row = await conn.fetchrow("SELECT conditions, assign_to_dept_code, dest_dept_code FROM workflow_rules WHERE id = $1", rid)
    conds = json.loads(row["conditions"])
    assert conds == [
        {"field": "dest_dept_code", "op": "eq", "value": 901},
        {"field": "origin_dept_code", "op": "in", "value": [901, 902]},
        {"field": "dest_dept_code", "op": "ne", "value": 902},
        {"field": "subject", "op": "contains", "value": "Archivo"},  # intacta
        {"field": "dest_dept", "op": "regex", "value": "^Arch"},  # textual: intacta
    ]
    assert (row["assign_to_dept_code"], row["dest_dept_code"]) == (901, 902)
    columnas = {
        r["columna"] for r in await conn.fetch("SELECT columna FROM dependencia_codigo_backfill WHERE fila_id = $1", rid)
    }
    assert {"conditions:textual", "conditions:code"} <= columnas  # a la vista del administrador


async def test_condicion_con_un_nombre_irresoluble_aborta(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    await _rule(conn, "Archivo", [{"field": "dest_dept", "op": "in", "value": "Archivo, Fantasma"}])
    with pytest.raises(asyncpg.RaiseError, match="Fantasma"):
        await _aplicar_019(conn)
    await _sin_019(conn)


# ---------------------------------------------------------------------------
# flow_events: append-only, solo se completa el código
# ---------------------------------------------------------------------------


async def test_flow_events_recibe_el_codigo_y_el_trigger_queda_habilitado(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    rid = await _radicado(conn)
    ev = await _event(conn, rid, "Archivo", "Fantasma")
    antes = await conn.fetchrow("SELECT tipo_tx, to_dept, from_dept, comentario, detalles, ts FROM flow_events WHERE id = $1", ev)
    await _aplicar_019(conn)

    despues = await conn.fetchrow("SELECT *, to_dept_code, from_dept_code FROM flow_events WHERE id = $1", ev)
    assert (despues["to_dept_code"], despues["from_dept_code"]) == (901, None)
    assert {k: despues[k] for k in antes.keys()} == dict(antes)  # el TEXTO del evento no se tocó
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("UPDATE flow_events SET comentario = 'x' WHERE id = $1", ev)
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("DELETE FROM flow_events WHERE id = $1", ev)


# ---------------------------------------------------------------------------
# Idempotencia, ancla y la restricción de la fase D
# ---------------------------------------------------------------------------


async def test_la_019_es_idempotente_y_ancla_la_evidencia_una_sola_vez(pre019: Any) -> None:
    slug, schema, conn = pre019
    await _dep(conn, 901, "Archivo")
    rid = await _radicado(conn)
    await _step(conn, rid, 1, "pending", "Archivo")
    await _step(conn, rid, 2, "completed", "Fantasma")
    await _event(conn, rid, "Archivo")
    await _aplicar_019(conn)
    n = await conn.fetchval("SELECT count(*) FROM dependencia_codigo_backfill")
    n_ubic = await conn.fetchval("SELECT count(*) FROM radicado_ubicacion_recalculo")
    assert n == 3  # to_dept de los 2 pasos + to_dept del evento

    await _aplicar_019(conn)
    assert await conn.fetchval("SELECT count(*) FROM dependencia_codigo_backfill") == n
    assert await conn.fetchval("SELECT count(*) FROM radicado_ubicacion_recalculo") == n_ubic

    async with conn.transaction():
        assert await runner.anclar_evidencia(conn, schema, _019, tenant_slug=slug) is True
        assert await runner.anclar_evidencia(conn, schema, _019, tenant_slug=slug) is False
    (ancla,) = await _asientos(conn, slug, "workflow.dependencia_codigo.backfill")
    filas = await conn.fetch("SELECT * FROM dependencia_codigo_backfill ORDER BY fila_id, id")
    columnas = [c for c in filas[0].keys() if c not in ("id", "at")]
    esperado = hashlib.sha256("\n".join("|".join(str(f[c]) for c in columnas) for f in filas).encode()).hexdigest()
    assert (ancla["payload"]["filas"], ancla["payload"]["sha256"]) == (n, esperado)
    # La fase C ancla con SU acción (si compartiera la de la 016, quedaría sin ancla).
    (ancla_ubic,) = await _asientos(conn, slug, "workflow.ubicacion_radicado.backfill_019")
    assert ancla_ubic["payload"]["filas"] == n_ubic >= 1


async def test_la_evidencia_es_de_solo_insercion(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    rid = await _radicado(conn)
    await _step(conn, rid, 1, "pending", "Archivo")
    await _aplicar_019(conn)
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("UPDATE dependencia_codigo_backfill SET nombre = 'x'")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("DELETE FROM dependencia_codigo_backfill")


async def test_un_paso_vivo_sin_codigo_ya_no_puede_existir(pre019: Any) -> None:
    _, _, conn = pre019
    await _aplicar_019(conn)
    rid = await _radicado(conn)
    with pytest.raises(asyncpg.CheckViolationError, match="ck_flow_steps_vivo_con_codigo"):
        await conn.execute(
            "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, assigned_by, "
            "action, status) SELECT id, tracking_number, 1, 'X', gen_random_uuid(), 'assign', 'pending' "
            "FROM radicados WHERE id = $1",
            rid,
        )
    # Una regla sin código destino tampoco.
    with pytest.raises(asyncpg.CheckViolationError, match="ck_workflow_rules_assign_code"):
        await conn.execute(
            "INSERT INTO workflow_rules (name, assign_to_dept, created_by) VALUES ('r', 'X', gen_random_uuid())"
        )


async def test_sin_la_funcion_y_con_nombres_pendientes_falla_y_sin_ellos_no_hay_nada_que_perder(pre019: Any) -> None:
    _, _, conn = pre019
    await conn.execute("DROP FUNCTION resolver_codigo_dependencia(text, boolean)")
    await _aplicar_019(conn)  # sin filas pendientes: no lanza
    await conn.execute("DROP TABLE flow_steps CASCADE")  # estado limpio para repetir con datos
    await conn.execute(
        (runner.TENANT_DIR / "001_create_workflow.sql").read_text()
    )
    rid = await _radicado(conn)
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, assigned_by, action, status) "
        "SELECT id, tracking_number, 1, 'Archivo', gen_random_uuid(), 'assign', 'completed' FROM radicados WHERE id = $1",
        rid,
    )
    with pytest.raises(asyncpg.RaiseError, match="tenant-service"):
        await _aplicar_019(conn)


# ---------------------------------------------------------------------------
# Fase C: la ubicación sale del CÓDIGO del paso
# ---------------------------------------------------------------------------


async def test_fase_c_corrige_el_codigo_de_la_ubicacion_y_deja_evidencia_propia(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    rid = await _radicado(conn)
    await _step(conn, rid, 1, "pending", "Archivo")
    # La 016 dejó el nombre sin código (p. ej. era ambiguo entonces).
    await conn.execute("UPDATE radicados SET flujo_dept = 'Archivo', flujo_dept_code = NULL WHERE id = $1", rid)
    await _aplicar_019(conn)
    r = await conn.fetchrow("SELECT flujo_dept, flujo_dept_code, ubicacion_dept_code FROM radicados WHERE id = $1", rid)
    assert (r["flujo_dept"], r["flujo_dept_code"], r["ubicacion_dept_code"]) == ("Archivo", 901, 901)
    ev = await conn.fetchrow("SELECT * FROM radicado_ubicacion_recalculo WHERE radicado_id = $1", rid)
    assert (ev["origen"], ev["antes_dept_code"], ev["despues_dept_code"]) == ("migracion_019", None, 901)


# ---------------------------------------------------------------------------
# COMPORTAMIENTO (schema ya migrado): renombrar no rompe nada
# ---------------------------------------------------------------------------


async def _dependencias(conn: asyncpg.Connection, *filas: tuple[int, str]) -> None:
    for codigo, nombre in filas:
        await conn.execute("INSERT INTO dependencias (codigo, nombre) VALUES ($1, $2)", codigo, nombre)


async def _renombrar(conn: asyncpg.Connection, codigo: int, nuevo: str) -> None:
    """Lo que hace PATCH /dependencias/{id}: nombre nuevo + el anterior al historial."""
    await conn.execute(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
        "SELECT codigo, nombre, 'renombre' FROM dependencias WHERE codigo = $1",
        codigo,
    )
    await conn.execute("UPDATE dependencias SET nombre = $2 WHERE codigo = $1", codigo, nuevo)


async def test_renombrar_con_tramite_vivo_no_saca_el_paso_de_la_bandeja_ni_de_la_ubicacion(ctx: Any) -> None:
    """T1. Mutación: `_user_depts_subquery` vuelto a `d.nombre` (o `to_dept IN`) deja la
    bandeja vacía en cuanto la dependencia se renombra."""
    svc, conn, _ = ctx
    await _dependencias(conn, (901, "Oficina Archivo"))
    miembro, rad = await _user(conn, 901), await _user(conn)
    rid = await _radicado(conn)
    await svc.assign(_assign(rid, "Oficina Archivo", None), rad)  # paso VIVO, sin dueño, a 901

    async def bandeja() -> list:
        items, _ = await svc.inbox(box=None, assigned_to=None, page=1, size=20, user_id=miembro)
        return items

    assert [s.radicado_id for s in await bandeja()] == [rid]  # control positivo antes del renombre

    await _renombrar(conn, 901, "Gestión Documental")

    items = await bandeja()
    assert [s.radicado_id for s in items] == [rid]
    assert (items[0].to_dept, items[0].to_dept_code) == ("Oficina Archivo", 901)  # instantánea + llave
    u = await conn.fetchrow("SELECT flujo_dept, flujo_dept_code, ubicacion_dept_code FROM radicados WHERE id = $1", rid)
    assert (u["flujo_dept"], u["flujo_dept_code"], u["ubicacion_dept_code"]) == ("Oficina Archivo", 901, 901)
    # Y sigue siendo SUYO: el miembro lo cierra (tenencia por código).
    cerrado = await svc.complete_step(items[0].id, __import__("app.schemas.workflow", fromlist=["FlowStepComplete"]).FlowStepComplete(), miembro)
    assert cerrado.status == "completed"


async def test_un_miembro_de_otra_dependencia_no_ve_ni_cierra_el_paso(ctx: Any) -> None:
    svc, conn, _ = ctx
    await _dependencias(conn, (901, "Oficina A"), (902, "Oficina B"))
    ajeno, rad = await _user(conn, 902), await _user(conn)
    rid = await _radicado(conn)
    await svc.assign(_assign(rid, "Oficina A", None), rad)
    items, _ = await svc.inbox(box=None, assigned_to=None, page=1, size=20, user_id=ajeno)
    assert items == []


async def test_el_alias_por_nombre_no_desempata_homonimas_ni_revive_nombres_viejos(ctx: Any) -> None:
    svc, conn, _ = ctx
    await _dependencias(conn, (901, "Oficina A"), (902, "Oficina A"))
    rad = await _user(conn)
    rid = await _radicado(conn)
    with pytest.raises(HTTPException) as exc:
        await svc.assign(_assign(rid, "Oficina A", None), rad)
    assert exc.value.detail["error"] == "dependencia_ambigua"
    assert await conn.fetchval("SELECT count(*) FROM flow_steps") == 0

    # Un nombre ANTERIOR no sirve como destino de una acción (solo para leer/replay).
    await conn.execute("DELETE FROM dependencias WHERE codigo = 902")
    await _renombrar(conn, 901, "Gestión Documental")
    with pytest.raises(HTTPException) as exc:
        await svc.assign(_assign(rid, "Oficina A", None), rad)
    assert exc.value.detail["error"] == "dependencia_desconocida"


async def test_el_origen_lo_deriva_el_servidor_y_uno_desactualizado_es_409(ctx: Any) -> None:
    """T9. Mutación: confiar en el `from_dept_code` del cliente deja pasar el 409."""
    svc, conn, _ = ctx
    await _dependencias(conn, (901, "Oficina A"), (902, "Oficina B"), (903, "Oficina C"))
    rad, miembro = await _user(conn), await _user(conn, 901)
    rid = await _radicado(conn)
    primero = await svc.assign(_assign(rid, "Oficina A", None), rad)

    pasos = _assign(rid, "Oficina B", None, action="transfer", from_dept_code=903)
    with pytest.raises(HTTPException) as exc:
        await svc.transfer(pasos, miembro)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "origen_desactualizado"

    # Sin origen en el cuerpo: el del paso activo (901).
    segundo = await svc.transfer(_assign(rid, "Oficina B", None, action="transfer"), miembro)
    assert (segundo.from_dept, segundo.from_dept_code, segundo.to_dept_code) == ("Oficina A", 901, 902)
    assert primero.to_dept_code == 901
    ev = await conn.fetchrow(
        "SELECT from_dept_code, to_dept_code FROM flow_events WHERE tipo_tx = 'transfer'"
    )
    assert (ev["from_dept_code"], ev["to_dept_code"]) == (901, 902)


async def test_el_nombre_guardado_es_el_del_catalogo_no_el_que_escribio_el_cliente(ctx: Any) -> None:
    svc, conn, _ = ctx
    await _dependencias(conn, (901, "Oficina A"))
    rad = await _user(conn)
    rid = await _radicado(conn)
    with pytest.raises(HTTPException) as exc:  # nombre y código discordantes
        await svc.assign(_assign(rid, "oficina a", None, to_dept_code=901), rad)
    assert exc.value.detail["error"] == "dependencia_inconsistente"
    paso = await svc.assign(_assign(rid, "Oficina A", None), rad)
    assert (paso.to_dept, paso.to_dept_code) == ("Oficina A", 901)


async def test_ubicacion_del_servicio_es_el_gemelo_de_la_fase_c_de_la_019(pre019: Any) -> None:
    """T14. Un corpus con la forma de los pasos reales: lo que escribe la fase C de la 019 es
    exactamente lo que devuelve `derivar_ubicacion` (mismo desempate). Mutación: cambiar el
    desempate en uno de los dos."""
    _, _, conn = pre019
    await _dep(conn, 901, "A")
    await _dep(conn, 902, "B")
    casos = []
    for status, pasos in (
        ("distributed", [(1, "returned", "A", 5), (2, "pending", "B", None)]),
        ("distributed", [(1, "completed", "A", 30), (2, "cancelled", "B", 10)]),  # rollback + completar
        ("distributed", [(1, "completed", "A", 5), (2, "completed", "B", 5)]),  # empate de cierre
        ("registered", []),
    ):
        rid = await _radicado(conn, status)
        for n, st, dept, minutos in pasos:
            await conn.execute(
                "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, assigned_by, "
                "action, status, completed_at) SELECT id, tracking_number, $2, $3, gen_random_uuid(), 'assign', $4::text, "
                "CASE WHEN $5::int IS NULL THEN NULL ELSE timestamptz '2026-10-01 08:00+00' + $5 * interval '1 minute' END "
                "FROM radicados WHERE id = $1",
                rid, n, dept, st, minutos,
            )
        casos.append((rid, status))
    await _aplicar_019(conn)
    for rid, status in casos:
        pasos = [
            PasoUbicacion(
                step_number=p["step_number"], status=p["status"], to_dept=p["to_dept"],
                assigned_to=p["assigned_to"], completed_at=p["completed_at"], to_dept_code=p["to_dept_code"],
            )
            for p in await conn.fetch("SELECT * FROM flow_steps WHERE radicado_id = $1", rid)
        ]
        esperado = derivar_ubicacion(status, pasos)
        r = await conn.fetchrow("SELECT flujo_dept, flujo_dept_code, flujo_usuario FROM radicados WHERE id = $1", rid)
        real = Ubicacion(r["flujo_dept"], r["flujo_dept_code"], r["flujo_usuario"]) if r["flujo_dept"] else None
        assert real == esperado, (status, pasos)


# ---------------------------------------------------------------------------
# 020: la evidencia de la reescritura guarda el JSON completo y enlaza el mapeo
# ---------------------------------------------------------------------------


async def test_la_020_reconstruye_antes_y_despues_sin_truncar_y_es_idempotente(pre019: Any) -> None:
    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    larga = [{"field": "subject", "op": "contains", "value": "x" * 2500}]
    original = [{"field": "dest_dept", "op": "eq", "value": "Archivo"}, *larga]
    rid = await _rule(conn, "Archivo", [{"field": "dest_dept", "op": "eq", "value": "Archivo"}])
    rid_larga = await _rule(conn, "Archivo", original)
    await _aplicar_019(conn)

    await conn.execute(_sql_020())
    await conn.execute(_sql_020())  # idempotente

    corta = await conn.fetchrow(
        "SELECT * FROM dependencia_codigo_backfill WHERE fila_id = $1 AND columna = 'conditions:code'", rid
    )
    assert json.loads(corta["antes"]) == [{"field": "dest_dept", "op": "eq", "value": "Archivo"}]
    assert json.loads(corta["despues"]) == [{"field": "dest_dept_code", "op": "eq", "value": 901}]
    assert corta["reconstruido"] is True
    # La regla larga: el texto guardado por la 019 está truncado -> `antes` NO se inventa.
    largo = await conn.fetchrow(
        "SELECT * FROM dependencia_codigo_backfill WHERE fila_id = $1 AND columna = 'conditions:code'", rid_larga
    )
    assert largo["antes"] is None and largo["reconstruido"] is True
    assert json.loads(largo["despues"])[0] == {"field": "dest_dept_code", "op": "eq", "value": 901}
    # El trigger de solo inserción quedó habilitado tras completar las columnas nuevas.
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("UPDATE dependencia_codigo_backfill SET antes = NULL")


async def test_la_020_no_toca_las_columnas_que_ancla_la_019_y_tiene_ancla_propia(pre019: Any) -> None:
    """Mutación: que la 020 reescriba `resultado`/`codigo`/`nombre` cambia el SHA-256 del ancla de la 019."""
    import hashlib

    cols = "tabla, fila_id, columna, nombre, codigo, candidatos, resultado, vivo"
    nombres = [c.strip() for c in cols.split(",")]

    async def huella(conn: Any) -> str:
        filas = await conn.fetch(f"SELECT {cols} FROM dependencia_codigo_backfill ORDER BY fila_id, id")
        contenido = "\n".join("|".join(str(f[c]) for c in nombres) for f in filas)
        return hashlib.sha256(contenido.encode("utf-8")).hexdigest()

    _, _, conn = pre019
    await _dep(conn, 901, "Archivo")
    await _dep(conn, 902, "Archivo", activo=False)
    rid = await _radicado(conn)
    await _step(conn, rid, 1, "pending", "Archivo")
    await conn.fetchval(
        "INSERT INTO dependencia_nombre_historico (codigo, nombre, origen) "
        "VALUES (901, 'Archivo', 'mapeo_manual') RETURNING id"
    )
    await _rule(conn, "Archivo", [{"field": "dest_dept", "op": "eq", "value": "Archivo"}])
    await _aplicar_019(conn)
    antes = await huella(conn)
    await conn.execute(_sql_020())
    assert await huella(conn) == antes
    (nueva,) = runner._ANCLAS["020_backfill_evidencia_completa.sql"]
    assert nueva[1] == "workflow.dependencia_codigo.backfill_020"
    assert nueva[1] != runner._ANCLAS["019_flujo_dependencia_por_codigo.sql"][0][1]
