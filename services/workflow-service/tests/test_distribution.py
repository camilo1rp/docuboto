# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de distribución automática por reglas (E05, RF-FLU-04)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.distribution import auto_distribute
from tests.conftest import RADICADO_ID, TRACKING_NUMBER, make_step_record

NOW = datetime.now(tz=UTC)


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _rule_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "name": "Entradas a Jurídica",
        "description": None,
        "is_active": True,
        "priority": 100,
        "conditions": json.dumps([{"field": "doc_type", "op": "eq", "value": "E"}]),
        "match_mode": "all",
        "doc_type": None,
        "doc_class": None,
        "dest_dept": None,
        "dest_dept_code": None,
        "subject_contains": None,
        "assign_to_dept": "Dirección Jurídica",
        "assign_to_dept_code": 4,
        "assign_notes": "auto",
        "created_by": uuid.uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


_PAYLOAD = {
    "id": str(RADICADO_ID),
    "tracking_number": TRACKING_NUMBER,
    "doc_type": "E",
    "subject": "Solicitud",
    "dest_dept": "Ventanilla",
    "dest_dept_code": 1,
}


@pytest.mark.asyncio
async def test_auto_distribute_assigns_on_match() -> None:
    conn = _conn()
    conn.fetchval.return_value = 1  # get_next_step_number (sin pasos previos)
    conn.fetch.return_value = [_rule_row()]  # reglas activas
    conn.fetchrow.return_value = make_step_record(action="assign")  # INSERT paso

    dept = await auto_distribute(conn, "demo", _PAYLOAD)
    assert dept == "Dirección Jurídica"


@pytest.mark.asyncio
async def test_auto_distribute_idempotent_when_already_has_steps() -> None:
    conn = _conn()
    conn.fetchval.return_value = 2  # ya tiene pasos → no redistribuir

    dept = await auto_distribute(conn, "demo", _PAYLOAD)
    assert dept is None


@pytest.mark.asyncio
async def test_auto_distribute_no_rule_match() -> None:
    conn = _conn()
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [
        _rule_row(conditions=json.dumps([{"field": "doc_type", "op": "eq", "value": "S"}]))
    ]

    dept = await auto_distribute(conn, "demo", _PAYLOAD)
    assert dept is None


@pytest.mark.asyncio
async def test_auto_distribute_rule_dept_no_catalogado_no_rompe_el_motor() -> None:
    """Cierre del agujero de disponibilidad (`_create_step` ahora valida
    `to_dept` contra `dependencias`): si una regla vieja/con errata apunta a
    una dependencia que NO está activa en el catálogo, el motor de
    auto-distribución NO debe lanzar ni dejar el mensaje reintentando en la
    PEL contra un dato permanentemente inválido — se resuelve como "sin
    distribuir" (mismo resultado que `no_rule_match`), y se loguea para que se
    corrija la regla.

    `conn.fetchval.side_effect`: 1) get_next_step_number → 1 (no distribuido
    aún); 2) dependencia_activa(assign_to_dept) → None (no está en el catálogo).
    """
    conn = _conn()
    conn.fetchval.side_effect = [1, None]
    conn.fetch.return_value = [_rule_row(assign_to_dept="Dependencia Fantasma")]

    dept = await auto_distribute(conn, "demo", _PAYLOAD)

    assert dept is None
    conn.fetchrow.assert_not_awaited()  # nunca llega a WorkflowService.assign


@pytest.mark.asyncio
async def test_auto_distribute_rule_dept_no_catalogado_deja_traza_audit_log() -> None:
    """Segunda mitad del residual (cerrado 2026-07-30): la rama de degradación
    silenciosa (solo `logger.warning`) ahora además deja un asiento en
    `public.audit_log` (Canal A) — verificado por mutación real (`audit.append`
    hace `execute` para el advisory lock + INSERT, no mockeado aparte).

    `conn.fetchval.side_effect`: 1) get_next_step_number → 1; 2)
    dependencia_activa(assign_to_dept) → None; 3) `audit.append`'s prev_hash
    lookup → None (cadena vacía)."""
    conn = _conn()
    conn.fetchval.side_effect = [1, None, None]
    conn.fetch.return_value = [_rule_row(assign_to_dept="Dependencia Fantasma")]

    dept = await auto_distribute(conn, "demo", _PAYLOAD)

    assert dept is None
    conn.fetchrow.assert_not_awaited()
    # execute() se llama para: SET search_path (x2, dentro de get_next_step_number
    # y dependencia_activa) + pg_advisory_xact_lock + INSERT INTO audit_log.
    insert_calls = [
        c for c in conn.execute.await_args_list if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(insert_calls) == 1


@pytest.mark.asyncio
async def test_auto_distribute_audit_append_fallido_no_rompe_la_degradacion() -> None:
    """Best-effort a propósito: si `audit.append` falla (p. ej. error transitorio
    de BD), `auto_distribute` sigue devolviendo `None` (mismo resultado que sin
    la traza) — un fallo de AUDITORÍA no debe escalar a "reintentar el mensaje"
    contra una regla permanentemente inválida (eso sería ruido de DLQ)."""
    conn = _conn()
    conn.fetchval.side_effect = [1, None, RuntimeError("db down")]
    conn.fetch.return_value = [_rule_row(assign_to_dept="Dependencia Fantasma")]

    dept = await auto_distribute(conn, "demo", _PAYLOAD)

    assert dept is None
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_distribute_sigue_funcionando_con_dependencia_valida() -> None:
    """Control positivo del motor tras el cierre: una regla que apunta a una
    dependencia SÍ activa en el catálogo sigue autoasignando sin cambios de
    comportamiento (gemelo de `test_auto_distribute_assigns_on_match`, pero
    con `dependencia_activa` diferenciada explícitamente en el side_effect en
    vez de depender del `return_value` blanket)."""
    conn = _conn()
    # 1) get_next_step_number → 1; 2) dependencia_activa(to_dept) → True (dentro
    # de auto_distribute); 3) get_radicado_tracking_number (_create_step) → OK;
    # 4) dependencia_activa(to_dept) (_create_step) → True; 5) get_next_step_number
    # (_create_step) → 1; 6) audit.append prev_hash → None.
    # RN-8: entre 3) y 4) `_create_step` re-comprueba «sin pasos» DENTRO del cerrojo
    # (otro `get_next_step_number` → 1).
    conn.fetchval.side_effect = [1, 1, TRACKING_NUMBER, 1, 1, 1, None]
    conn.fetch.return_value = [_rule_row()]
    conn.fetchrow.return_value = make_step_record(action="assign")

    dept = await auto_distribute(conn, "demo", _PAYLOAD)

    assert dept == "Dirección Jurídica"


@pytest.mark.asyncio
async def test_rn8_la_guarda_autoritativa_va_dentro_del_cerrojo() -> None:
    """RN-8: la guarda de `auto_distribute` (fuera del cerrojo) la pasan DOS entregas
    simultáneas del mismo evento; la segunda ya encuentra el radicado repartido al
    re-comprobar dentro de `_create_step` y NO crea otro paso ni asiento.

    `conn.fetchval`: 1) guarda externa → 1 (aún sin pasos); 2) dependencia_activa;
    3) tracking (`_create_step`); 4) `get_next_step_number` DENTRO del cerrojo → 2
    (la otra entrega ya lo repartió)."""
    conn = _conn()
    conn.fetchval.side_effect = [1, 1, TRACKING_NUMBER, 2]
    conn.fetch.return_value = [_rule_row()]

    dept = await auto_distribute(conn, "demo", _PAYLOAD)

    assert dept is None
    conn.fetchrow.assert_not_awaited()  # ni INSERT del paso ni lectura posterior
    assert not [
        c for c in conn.execute.await_args_list if "INSERT INTO public.audit_log" in str(c.args[0])
    ]


# ---------------------------------------------------------------------------
# Códigos del evento (ADR-032): código del payload -> nombre con historial -> ausente
# ---------------------------------------------------------------------------

_SIN_CODIGO = {k: v for k, v in _PAYLOAD.items() if k != "dest_dept_code"}


def _regla_por_codigo(op: str, valor: object) -> dict:
    return _rule_row(
        conditions=json.dumps([{"field": "dest_dept_code", "op": op, "value": valor}])
    )


@pytest.mark.asyncio
async def test_evento_viejo_sin_codigo_se_resuelve_por_el_nombre_con_historial() -> None:
    """Un evento de la cola de fallidos anterior a la 046 trae solo `dest_dept`. Si el
    nombre (incluidos los anteriores y los mapeos manuales) resuelve a UNA dependencia,
    las reglas por código casan."""
    conn = _conn()
    conn.fetchval.side_effect = [1, 1, TRACKING_NUMBER, 1, 1, 1, None]
    conn.fetch.return_value = [_regla_por_codigo("eq", 3)]
    conn.fetchrow.side_effect = [
        {"codigo": 3, "candidatos": 1},  # resolver_codigo_dependencia(dest_dept, TRUE)
        make_step_record(action="assign"),
    ]

    assert await auto_distribute(conn, "demo", _SIN_CODIGO) == "Dirección Jurídica"
    sql, nombre, historico = conn.fetchrow.await_args_list[0].args
    assert "resolver_codigo_dependencia" in sql and nombre == "Ventanilla"
    assert historico is True  # el replay SÍ ve nombres anteriores


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["eq", "ne", "in"])
async def test_nombre_ambiguo_no_da_codigo_y_las_condiciones_por_codigo_no_casan(op: str) -> None:
    """Fail-closed: con un destino que se desconoce, ni `ne` («no es X») se da por cierto.
    Mutación: quitar la guarda `CODE_FIELDS` de `eval_condition` hace casar `ne`."""
    conn = _conn()
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [_regla_por_codigo(op, [3] if op == "in" else 3)]
    conn.fetchrow.return_value = {"codigo": None, "candidatos": 2}

    assert await auto_distribute(conn, "demo", _SIN_CODIGO) is None
    assert not any("INSERT INTO flow_steps" in c.args[0] for c in conn.fetchrow.await_args_list)


@pytest.mark.asyncio
async def test_regla_legada_por_destino_se_compara_por_codigo() -> None:
    conn = _conn()
    conn.fetchval.side_effect = [1, 1, TRACKING_NUMBER, 1, 1, 1, None]
    conn.fetch.return_value = [_rule_row(conditions="[]", dest_dept="Ventanilla", dest_dept_code=1)]
    conn.fetchrow.return_value = make_step_record(action="assign")
    assert await auto_distribute(conn, "demo", _PAYLOAD) == "Dirección Jurídica"

    # El mismo nombre en el evento pero OTRO código: no casa (el nombre ya no cuenta).
    conn2 = _conn()
    conn2.fetchval.return_value = 1
    conn2.fetch.return_value = [_rule_row(conditions="[]", dest_dept="Ventanilla", dest_dept_code=2)]
    assert await auto_distribute(conn2, "demo", _PAYLOAD) is None
