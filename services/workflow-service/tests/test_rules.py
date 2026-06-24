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
async def test_evaluate_missing_tenant_is_400(client_with_conn) -> None:
    client, _, _ = client_with_conn
    resp = await client.post("/api/v1/workflow/rules/evaluate", json=_EVAL_REQ)
    assert resp.status_code == 400
