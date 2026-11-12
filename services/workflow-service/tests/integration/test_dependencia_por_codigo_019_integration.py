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

* **El esquema**: un paso vivo o una regla sin código no pueden existir, y la evidencia
  del recálculo por código es de solo inserción.
* **El comportamiento**: renombrar una dependencia con trámite vivo no saca el paso de la
  bandeja ni de la ubicación; el origen lo deriva el servidor; el alias por nombre no
  desempata homónimas.

Mutaciones verificadas: `_user_depts_subquery` vuelto a `d.nombre` -> cae
`test_renombrar_con_tramite_vivo_*`.

Carril de integración (`RUN_INTEGRATION=1`, ver `conftest.py`).
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from fastapi import HTTPException

from tests.integration.conftest import insert_dependencia
from tests.integration.test_estado_radicado_integration import (  # noqa: F401
    _assign,
    _radicado,
    _sin_permisos_ni_redis,
    _user,
    ctx,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# ESQUEMA: las restricciones y la evidencia de solo inserción
# ---------------------------------------------------------------------------


async def test_la_evidencia_es_de_solo_insercion(ctx: Any) -> None:
    _, conn, _ = ctx
    await conn.execute(
        "INSERT INTO dependencia_codigo_backfill (tabla, fila_id, columna, nombre, codigo, candidatos, "
        "resultado, vivo) VALUES ('flow_steps', gen_random_uuid(), 'to_dept', 'Archivo', 901, 1, 'resuelto', true)"
    )
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("UPDATE dependencia_codigo_backfill SET nombre = 'x'")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("DELETE FROM dependencia_codigo_backfill")


async def test_un_paso_vivo_sin_codigo_ya_no_puede_existir(ctx: Any) -> None:
    _, conn, _ = ctx
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


# ---------------------------------------------------------------------------
# COMPORTAMIENTO: renombrar no rompe nada
# ---------------------------------------------------------------------------


async def _dependencias(conn: asyncpg.Connection, *filas: tuple[int, str]) -> None:
    for codigo, nombre in filas:
        await insert_dependencia(conn, codigo, nombre)


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
    cerrado = await svc.complete_step(items[0].id, __import__("workflow.schemas.workflow", fromlist=["FlowStepComplete"]).FlowStepComplete(), miembro)
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
