# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del motor de reglas (E05): operadores, AND/OR, batch y compat legada."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.services.rules_engine import (
    eval_condition,
    matches_conditions,
    matches_legacy,
    rule_matches,
)
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Motor puro (sin BD)
# ---------------------------------------------------------------------------
def _ctx(**kw):
    base = {
        f: None
        for f in (
            "doc_type",
            "doc_class",
            "dest_dept",
            "origin_dept",
            "subject",
            "sender_name",
            "sender_entity",
            "pages",
        )
    }
    base.update(kw)
    return base


def test_op_eq_and_ne():
    assert eval_condition({"field": "doc_type", "op": "eq", "value": "E"}, _ctx(doc_type="E"))
    assert not eval_condition({"field": "doc_type", "op": "eq", "value": "S"}, _ctx(doc_type="E"))
    assert eval_condition({"field": "doc_type", "op": "ne", "value": "S"}, _ctx(doc_type="E"))


def test_op_contains():
    c = {"field": "subject", "op": "contains", "value": "tutela"}
    assert eval_condition(c, _ctx(subject="Acción de TUTELA urgente"))
    assert not eval_condition(c, _ctx(subject="Derecho de petición"))


def test_op_in():
    c = {"field": "doc_type", "op": "in", "value": "S,I"}
    assert eval_condition(c, _ctx(doc_type="I"))
    assert not eval_condition(c, _ctx(doc_type="E"))
    # value como lista también
    assert eval_condition(
        {"field": "doc_type", "op": "in", "value": ["E", "S"]}, _ctx(doc_type="E")
    )


def test_op_numeric():
    assert eval_condition({"field": "pages", "op": "gt", "value": 10}, _ctx(pages=20))
    assert not eval_condition({"field": "pages", "op": "gt", "value": 30}, _ctx(pages=20))
    assert eval_condition({"field": "pages", "op": "lte", "value": 20}, _ctx(pages=20))
    # dato no numérico → False, no excepción
    assert not eval_condition({"field": "pages", "op": "gt", "value": "x"}, _ctx(pages=20))


def test_op_regex():
    c = {"field": "subject", "op": "regex", "value": r"^EXP-\d+"}
    assert eval_condition(c, _ctx(subject="EXP-2024 algo"))
    assert not eval_condition(c, _ctx(subject="otro"))
    # regex inválida → False
    assert not eval_condition({"field": "subject", "op": "regex", "value": "("}, _ctx(subject="x"))


def test_match_mode_all_vs_any():
    conds = [
        {"field": "doc_type", "op": "eq", "value": "E"},
        {"field": "dest_dept", "op": "eq", "value": "Jurídica"},
    ]
    ctx = _ctx(doc_type="E", dest_dept="Otra")
    assert not matches_conditions(conds, "all", ctx)  # AND falla
    assert matches_conditions(conds, "any", ctx)  # OR pasa por doc_type


def test_legacy_fallback():
    rule = {"doc_type": "E", "doc_class": None, "dest_dept": None, "subject_contains": "pago"}
    assert matches_legacy(rule, _ctx(doc_type="E", subject="solicitud de PAGO"))
    assert not matches_legacy(rule, _ctx(doc_type="S", subject="solicitud de pago"))


def test_rule_matches_dispatch():
    # con conditions → usa operadores
    rule = {"match_mode": "all"}
    conds = [{"field": "doc_type", "op": "eq", "value": "E"}]
    assert rule_matches(rule, conds, _ctx(doc_type="E"))
    # sin conditions → usa legado
    legacy_rule = {"doc_type": "E", "match_mode": "all"}
    assert rule_matches(legacy_rule, [], _ctx(doc_type="E"))


# ---------------------------------------------------------------------------
# API (BD mockeada)
# ---------------------------------------------------------------------------
def make_rule_record(
    *,
    rule_id: uuid.UUID | None = None,
    name: str = "Tutelas a Jurídica",
    conditions: list | None = None,
    match_mode: str = "all",
    doc_type: str | None = None,
    doc_class: str | None = None,
    dest_dept: str | None = None,
    subject_contains: str | None = None,
    assign_to_dept: str = "Dirección Jurídica",
    assign_notes: str | None = None,
) -> dict:
    return {
        "id": rule_id or uuid.uuid4(),
        "name": name,
        "description": None,
        "is_active": True,
        "priority": 100,
        "conditions": json.dumps(conditions or []),  # asyncpg devuelve JSONB como str
        "match_mode": match_mode,
        "doc_type": doc_type,
        "doc_class": doc_class,
        "dest_dept": dest_dept,
        "subject_contains": subject_contains,
        "assign_to_dept": assign_to_dept,
        "assign_notes": assign_notes,
        "created_by": uuid.uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }


_EVAL_REQ = {
    "radicado_id": str(uuid.uuid4()),
    "tracking_number": "2024-DEMO-E-000001",
    "doc_type": "E",
    "subject": "Acción de tutela",
    "pages": 12,
}


@pytest.mark.asyncio
async def test_evaluate_matches_with_operators(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    rule = make_rule_record(
        conditions=[
            {"field": "doc_type", "op": "eq", "value": "E"},
            {"field": "subject", "op": "contains", "value": "tutela"},
            {"field": "pages", "op": "gt", "value": 10},
        ],
        assign_to_dept="Jurídica",
    )
    mock_conn.fetch.return_value = [rule]

    resp = await client.post(
        "/api/v1/workflow/rules/evaluate", json=_EVAL_REQ, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True
    assert body["assign_to_dept"] == "Jurídica"
    mock_conn.execute.assert_awaited()  # activación registrada


@pytest.mark.asyncio
async def test_evaluate_no_match(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        make_rule_record(conditions=[{"field": "doc_type", "op": "eq", "value": "S"}])
    ]

    resp = await client.post(
        "/api/v1/workflow/rules/evaluate", json=_EVAL_REQ, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["matched"] is False


@pytest.mark.asyncio
async def test_evaluate_batch(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        make_rule_record(conditions=[{"field": "doc_type", "op": "eq", "value": "E"}])
    ]
    payload = {
        "items": [
            {"radicado_id": str(uuid.uuid4()), "tracking_number": "T1", "doc_type": "E"},
            {"radicado_id": str(uuid.uuid4()), "tracking_number": "T2", "doc_type": "S"},
        ]
    }
    resp = await client.post(
        "/api/v1/workflow/rules/evaluate/batch", json=payload, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 2
    assert results[0]["matched"] is True
    assert results[1]["matched"] is False


@pytest.mark.asyncio
async def test_create_rule_with_conditions(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_rule_record(
        rule_id=rid,
        conditions=[{"field": "doc_type", "op": "in", "value": "E,S"}],
        match_mode="any",
    )
    resp = await client.post(
        "/api/v1/workflow/rules",
        json={
            "name": "Entrada o Salida",
            "match_mode": "any",
            "conditions": [{"field": "doc_type", "op": "in", "value": "E,S"}],
            "assign_to_dept": "Ventanilla",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(rid)
    assert body["match_mode"] == "any"
    assert body["conditions"][0]["op"] == "in"


@pytest.mark.asyncio
async def test_evaluate_missing_tenant_is_403(client_with_conn) -> None:
    """`/evaluate` ahora cuelga de `require_permission` (Hallazgo B): sin
    X-Tenant-Slug esa dependency deniega explícitamente con 403 ANTES de que
    `_require_tenant` pueda dar su propio 400 — mismo comportamiento que
    `assign`/`transfer` (cf. `test_assign_missing_tenant_header`)."""
    client, _, _ = client_with_conn
    resp = await client.post("/api/v1/workflow/rules/evaluate", json=_EVAL_REQ)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Autorización del CRUD (USUA_PERM_ADMIN) — cierre de la brecha: antes ningún
# endpoint de este router estaba gateado y cualquier usuario autenticado del
# tenant podía crear/editar/borrar reglas de enrutamiento automático.
# `/evaluate` y `/evaluate/batch` (Hallazgo B, cerrado 2026-07-29): eran un
# oráculo del mismo CRUD (permiten reconstruir la tabla de enrutamiento
# iterando metadatos sintéticos) y ahora comparten el mismo gate — verificado
# que ningún llamante HTTP real existe (el motor de distribución usa la clase
# de servicio en proceso, no HTTP).
# ---------------------------------------------------------------------------

_CREATE_PAYLOAD = {
    "name": "Entrada o Salida",
    "match_mode": "any",
    "conditions": [{"field": "doc_type", "op": "in", "value": "E,S"}],
    "assign_to_dept": "Ventanilla",
}

_UPDATE_PAYLOAD = {"name": "Renombrada"}


@pytest.mark.asyncio
async def test_create_rule_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission → False
    resp = await client.post(
        "/api/v1/workflow/rules", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_rules_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get("/api/v1/workflow/rules", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_rule_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        f"/api/v1/workflow/rules/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_update_rule_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json=_UPDATE_PAYLOAD,
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_delete_rule_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.delete(
        f"/api/v1/workflow/rules/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_rules_con_permiso_ok(client_with_conn) -> None:
    """El administrador legítimo (fetchval por defecto = 1, ver conftest) sigue
    pudiendo operar el CRUD tras el cierre."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [make_rule_record()]
    resp = await client.get("/api/v1/workflow/rules", headers=TENANT_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_rule_con_permiso_ok(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1  # check_permission → True (admin)
    # delete_rule usa el resultado de execute() ("DELETE 1") para saber si borró.
    mock_conn.execute.return_value = "DELETE 1"
    resp = await client.delete(
        f"/api/v1/workflow/rules/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_evaluate_sin_permiso_403(client_with_conn) -> None:
    """Hallazgo B (cerrado): `/evaluate` es ahora el mismo oráculo que el CRUD
    protege — sin USUA_PERM_ADMIN, 403 fail-closed (fetchval=None ⇒
    check_permission → False), y las reglas NUNCA se leen (`fetch` no se invoca)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        "/api/v1/workflow/rules/evaluate", json=_EVAL_REQ, headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_con_permiso_ok(client_with_conn) -> None:
    """Control positivo: el administrador legítimo (fetchval=1, ver conftest)
    sigue pudiendo evaluar tras el cierre del Hallazgo B."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        make_rule_record(conditions=[{"field": "doc_type", "op": "eq", "value": "E"}])
    ]
    resp = await client.post(
        "/api/v1/workflow/rules/evaluate", json=_EVAL_REQ, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["matched"] is True


@pytest.mark.asyncio
async def test_evaluate_batch_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None
    payload = {
        "items": [
            {"radicado_id": str(uuid.uuid4()), "tracking_number": "T1", "doc_type": "E"},
        ]
    }
    resp = await client.post(
        "/api/v1/workflow/rules/evaluate/batch", json=payload, headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_batch_con_permiso_ok(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        make_rule_record(conditions=[{"field": "doc_type", "op": "eq", "value": "E"}])
    ]
    payload = {
        "items": [
            {"radicado_id": str(uuid.uuid4()), "tracking_number": "T1", "doc_type": "E"},
        ]
    }
    resp = await client.post(
        "/api/v1/workflow/rules/evaluate/batch", json=payload, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["matched"] is True


# ---------------------------------------------------------------------------
# `assign_to_dept` validado contra el catálogo `dependencias` (cierre del
# residual declarado en CLAUDE.md 2026-07-29): `POST`/`PATCH` ahora reutilizan
# `WorkflowRepository.dependencia_activa` (mismo helper que `_create_step`)
# ANTES de escribir la fila. Secuencia de `fetchval` en cada caso:
#   1) require_permission (USUA_PERM_ADMIN) → True
#   2) dependencia_activa(assign_to_dept) → True/None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_dependencia_inexistente_422(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]  # permiso OK, dependencia_activa → False

    resp = await client.post(
        "/api/v1/workflow/rules",
        json={**_CREATE_PAYLOAD, "assign_to_dept": "Dependencia Inventada"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["error"] == "dependencia_desconocida"
    assert "Dependencia Inventada" in body["detail"]
    mock_conn.fetchrow.assert_not_awaited()  # ninguna fila escrita si falla


@pytest.mark.asyncio
async def test_create_rule_dependencia_inactiva_422(client_with_conn) -> None:
    """`dependencia_activa` filtra `activo = TRUE` en la propia consulta SQL —
    una dependencia dada de baja es indistinguible de una inexistente desde la
    capa de servicio (`fetchval` devuelve `None` en ambos casos), mismo
    criterio ya establecido en `WorkflowService._validate_dept_catalog`."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]  # permiso OK, dependencia INACTIVA

    resp = await client.post(
        "/api/v1/workflow/rules",
        json={**_CREATE_PAYLOAD, "assign_to_dept": "Dependencia De Baja"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_desconocida"
    mock_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rule_dependencia_activa_ok(client_with_conn) -> None:
    """Control positivo: con la dependencia activa en el catálogo, el cierre no
    rompe el flujo de creación normal (gemelo de `test_create_rule_with_conditions`)."""
    client, mock_conn, _ = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.side_effect = [1, 1]  # permiso OK, dependencia_activa → True
    mock_conn.fetchrow.return_value = make_rule_record(rule_id=rid)

    resp = await client.post(
        "/api/v1/workflow/rules", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS
    )

    assert resp.status_code == 201
    assert resp.json()["id"] == str(rid)


@pytest.mark.asyncio
async def test_update_rule_sin_tocar_assign_to_dept_no_valida(client_with_conn) -> None:
    """PATCH parcial que NO incluye `assign_to_dept` no debe validarlo ni
    romperse — solo un `fetchval` (el del gate de permiso); `dependencia_activa`
    nunca se invoca porque el PATCH no tocó el campo."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1  # solo el gate de permiso
    mock_conn.fetchrow.return_value = make_rule_record(name="Renombrada")

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json=_UPDATE_PAYLOAD,  # {"name": "Renombrada"} — sin assign_to_dept
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert mock_conn.fetchval.await_count == 1


@pytest.mark.asyncio
async def test_update_rule_con_assign_to_dept_dependencia_inexistente_422(
    client_with_conn,
) -> None:
    """PATCH que SÍ toca `assign_to_dept` se valida igual que en `create_rule`."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]  # permiso OK, dependencia_activa → False

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json={"assign_to_dept": "Dependencia Inventada"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_desconocida"
    mock_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_rule_con_assign_to_dept_valido_ok(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, 1]  # permiso OK, dependencia_activa → True
    mock_conn.fetchrow.return_value = make_rule_record(assign_to_dept="Ventanilla Nueva")

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json={"assign_to_dept": "Ventanilla Nueva"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["assign_to_dept"] == "Ventanilla Nueva"
