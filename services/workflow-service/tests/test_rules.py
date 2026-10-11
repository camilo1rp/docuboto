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
from tests.conftest import TENANT_HEADERS, USER_ID

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
            "dest_dept_code",
            "origin_dept_code",
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
        {"field": "dest_dept_code", "op": "eq", "value": 4},
    ]
    ctx = _ctx(doc_type="E", dest_dept_code=9)
    assert not matches_conditions(conds, "all", ctx)  # AND falla
    assert matches_conditions(conds, "any", ctx)  # OR pasa por doc_type


def test_legacy_fallback():
    rule = {"doc_type": "E", "doc_class": None, "dest_dept_code": None, "subject_contains": "pago"}
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
    dest_dept_code: int | None = None,
    subject_contains: str | None = None,
    assign_to_dept: str = "Dirección Jurídica",
    assign_to_dept_code: int = 4,
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
        "dest_dept_code": dest_dept_code,
        "subject_contains": subject_contains,
        "assign_to_dept": assign_to_dept,
        "assign_to_dept_code": assign_to_dept_code,
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
            "assign_to_dept_code": 4,
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
    "assign_to_dept_code": 4,
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
    # 1) check_permission → True (admin); 2) prev_hash de `audit.append` → None.
    mock_conn.fetchval.side_effect = [1, None]
    # `delete_rule` usa `DELETE ... RETURNING *`: la fila borrada es lo que va al
    # asiento, porque después de borrarla no queda dónde ir a mirar qué decía.
    mock_conn.fetchrow.return_value = make_rule_record()
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
# La dependencia destino de la regla es un CÓDIGO (B5, ADR-032), validado contra el
# catálogo `dependencias` ANTES de escribir la fila; el nombre que se guarda es el del
# catálogo. Secuencia de `fetchval` en cada caso:
#   1) require_permission (USUA_PERM_ADMIN) → True
#   2) dependencia_activa(assign_to_dept_code) → nombre del catálogo / None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_dependencia_inexistente_422(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]  # permiso OK, dependencia_activa → None

    resp = await client.post(
        "/api/v1/workflow/rules",
        json={**_CREATE_PAYLOAD, "assign_to_dept_code": 987},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["error"] == "dependencia_desconocida"
    assert "987" in body["detail"]
    mock_conn.fetchrow.assert_not_awaited()  # ninguna fila escrita si falla


@pytest.mark.asyncio
async def test_create_rule_dependencia_inactiva_422(client_with_conn) -> None:
    """`dependencia_activa` filtra `activo = TRUE` en la propia consulta: una dependencia
    dada de baja es indistinguible de una inexistente desde la capa de servicio."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]

    resp = await client.post(
        "/api/v1/workflow/rules", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_desconocida"
    mock_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rule_dependencia_activa_ok_y_guarda_el_nombre_del_catalogo(
    client_with_conn,
) -> None:
    client, mock_conn, _ = client_with_conn
    rid = uuid.uuid4()
    # permiso OK, dependencia_activa → nombre del catálogo, prev_hash de `audit.append`
    mock_conn.fetchval.side_effect = [1, "Ventanilla Única", None]
    mock_conn.fetchrow.return_value = make_rule_record(rule_id=rid)

    resp = await client.post(
        "/api/v1/workflow/rules", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS
    )

    assert resp.status_code == 201
    assert resp.json()["id"] == str(rid)
    assert "deprecation" not in resp.headers
    insert = mock_conn.fetchrow.await_args_list[0].args
    assert "Ventanilla Única" in insert and 4 in insert  # nombre del catálogo + código


@pytest.mark.asyncio
async def test_create_rule_por_alias_ambiguo_422(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1]
    mock_conn.fetchrow.return_value = {"codigo": None, "candidatos": 2}
    payload = {k: v for k, v in _CREATE_PAYLOAD.items() if k != "assign_to_dept_code"}
    resp = await client.post(
        "/api/v1/workflow/rules",
        json={**payload, "assign_to_dept": "Archivo"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_ambigua"


@pytest.mark.asyncio
async def test_create_rule_por_alias_unico_marca_deprecation(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, "Archivo", None]
    mock_conn.fetchrow.side_effect = [{"codigo": 9, "candidatos": 1}, make_rule_record()]
    payload = {k: v for k, v in _CREATE_PAYLOAD.items() if k != "assign_to_dept_code"}
    resp = await client.post(
        "/api/v1/workflow/rules",
        json={**payload, "assign_to_dept": "Archivo"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.headers["deprecation"] == "true"


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["eq", "ne", "in"])
@pytest.mark.parametrize("field", ["dest_dept", "origin_dept"])
async def test_condicion_por_nombre_de_dependencia_se_rechaza(client_with_conn, op, field) -> None:
    """Comparar una dependencia por NOMBRE (eq/ne/in) cambiaba de sentido al renombrar: 422
    `condicion_por_nombre_obsoleta` que ofrece el campo `_code`."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1]
    resp = await client.post(
        "/api/v1/workflow/rules",
        json={**_CREATE_PAYLOAD, "conditions": [{"field": field, "op": op, "value": "Archivo"}]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "condicion_por_nombre_obsoleta"
    assert detail["campos"] == [f"{field}_code"]
    mock_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_condicion_textual_sobre_el_nombre_sigue_permitida(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, "Ventanilla", None]
    mock_conn.fetchrow.return_value = make_rule_record()
    resp = await client.post(
        "/api/v1/workflow/rules",
        json={
            **_CREATE_PAYLOAD,
            "conditions": [{"field": "dest_dept", "op": "contains", "value": "Archivo"}],
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_condicion_por_codigo_exige_enteros(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1]
    resp = await client.post(
        "/api/v1/workflow/rules",
        json={
            **_CREATE_PAYLOAD,
            "conditions": [{"field": "dest_dept_code", "op": "eq", "value": "Archivo"}],
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "condicion_invalida"


@pytest.mark.asyncio
async def test_update_rule_sin_tocar_el_destino_no_valida(client_with_conn) -> None:
    """PATCH parcial que NO incluye el destino no debe validarlo ni romperse — solo el
    gate de permiso y el asiento; `dependencia_activa` nunca se invoca."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.return_value = make_rule_record(name="Renombrada")

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json=_UPDATE_PAYLOAD,  # {"name": "Renombrada"} — sin destino
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert mock_conn.fetchval.await_count == 2


@pytest.mark.asyncio
async def test_update_rule_con_destino_inexistente_422(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]  # permiso OK, dependencia_activa → None

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json={"assign_to_dept_code": 987},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_desconocida"
    mock_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_rule_con_destino_valido_ok(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    # permiso OK, dependencia_activa → nombre, prev_hash del asiento → None
    mock_conn.fetchval.side_effect = [1, "Ventanilla Nueva", None]
    mock_conn.fetchrow.return_value = make_rule_record(assign_to_dept="Ventanilla Nueva")

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json={"assign_to_dept_code": 4},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["assign_to_dept"] == "Ventanilla Nueva"


# ---------------------------------------------------------------------------
# Auditoría del CRUD de reglas (ADR-010)
#
# Una regla decide a qué dependencia se asigna cada radicado ENTRANTE: quien la
# escribe está redirigiendo correspondencia oficial que todavía no ha llegado, y
# quien la borra hace que deje de redirigirse. Es de las mutaciones que ADR-010
# obliga a asentar, y este servicio no tenía ni una referencia a `audit` mientras
# su hermano `workflow_service` tenía cuarenta y nueve.
#
# Los asientos se miden sobre el `INSERT INTO public.audit_log` que emite
# `audit.append`, igual que en `test_workflow.py`.
# ---------------------------------------------------------------------------

# Posiciones de los argumentos del INSERT de `audit.append`. `args[0]` es el SQL
# y de ahí en adelante van `$1..$11` en el orden declarado en
# `shared/orpycamcp_common/audit.py`: tenant, service, actor, canal, action,
# object_type, object_ref, payload, prev_hash, hash, ts.
#
# Se nombran porque un índice suelto no se lee, y se comprueban contra el SQL en
# `test_los_indices_del_asiento_son_los_declarados`: escritos a ojo, el primer
# intento los tuvo desplazados y los tests fallaban por la razón equivocada.
_A_ACTOR = 3
_A_ACTION = 5
_A_OBJECT_TYPE = 6
_A_OBJECT_REF = 7
_A_PAYLOAD = 8


def test_los_indices_del_asiento_son_los_declarados() -> None:
    """Ancla los índices al SQL real, no a lo que yo recordara de él."""
    from orpycamcp_common.audit import _INSERT_SQL

    columnas = (
        _INSERT_SQL.split("(", 1)[1].split(")", 1)[0].replace("\n", " ").replace(" ", "")
    ).split(",")
    # `args[0]` es el SQL, así que la columna N-ésima va en `args[N + 1]`.
    assert columnas[_A_ACTOR - 1] == "actor"
    assert columnas[_A_ACTION - 1] == "action"
    assert columnas[_A_OBJECT_TYPE - 1] == "object_type"
    assert columnas[_A_OBJECT_REF - 1] == "object_ref"
    assert columnas[_A_PAYLOAD - 1] == "payload"


def _asientos(mock_conn) -> list:
    return [
        c
        for c in mock_conn.execute.await_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_crear_una_regla_deja_asiento_con_lo_que_la_regla_dice(client_with_conn) -> None:
    """El asiento lleva el CONTENIDO de la regla, no solo que se creó una.

    «Se creó la regla tal» no responde la pregunta que se le hace a la traza —
    «¿quién decidió que las tutelas fueran a Jurídica?»—; para eso hay que poder
    leer a dónde asignaba y con qué condiciones.
    """
    client, mock_conn, _ = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.side_effect = [1, 1, None]  # permiso, dependencia, prev_hash
    mock_conn.fetchrow.return_value = make_rule_record(
        rule_id=rid, name="Entrada o Salida", assign_to_dept="Ventanilla"
    )

    resp = await client.post(
        "/api/v1/workflow/rules", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS
    )

    assert resp.status_code == 201
    asientos = _asientos(mock_conn)
    assert len(asientos) == 1
    args = asientos[0].args
    assert args[_A_ACTION] == "workflow.regla_creada"
    assert args[_A_OBJECT_TYPE] == "workflow_rule"
    # El UUID y no el nombre: renombrar la regla no puede partir su historial.
    assert args[_A_OBJECT_REF] == str(rid)
    assert args[_A_ACTOR] == str(USER_ID)
    regla = json.loads(args[_A_PAYLOAD])["regla"]
    assert regla["assign_to_dept"] == "Ventanilla"
    assert regla["name"] == "Entrada o Salida"


@pytest.mark.asyncio
async def test_editar_una_regla_asienta_el_ANTES_y_el_DESPUES(client_with_conn) -> None:
    """Sin el valor anterior, el asiento dice que alguien la editó y nada más.

    Y solo los campos que esta petición tocó: volcar la regla entera enterraría
    el cambio —lo único que se viene a mirar— entre once campos que siguen igual.
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]  # permiso, prev_hash
    # 1.ª lectura: el estado previo (SELECT ... FOR UPDATE). 2.ª: la fila ya
    # actualizada que devuelve el UPDATE.
    mock_conn.fetchrow.side_effect = [
        make_rule_record(name="Nombre viejo", assign_to_dept="Jurídica"),
        make_rule_record(name="Renombrada", assign_to_dept="Jurídica"),
    ]

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}",
        json=_UPDATE_PAYLOAD,  # {"name": "Renombrada"}
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    args = _asientos(mock_conn)[0].args
    assert args[_A_ACTION] == "workflow.regla_modificada"
    cambios = json.loads(args[_A_PAYLOAD])["cambios"]
    assert cambios == {"name": {"antes": "Nombre viejo", "despues": "Renombrada"}}
    # El destino no se tocó, así que no aparece: el asiento describe el cambio.
    assert "assign_to_dept" not in cambios


@pytest.mark.asyncio
async def test_borrar_una_regla_conserva_en_el_asiento_lo_que_decia(client_with_conn) -> None:
    """El `DELETE` es físico: el asiento es el ÚNICO sitio donde sobrevive.

    Sin el retrato completo, «¿por qué dejaron de asignarse a Jurídica?» queda
    sin respuesta posible — la fila ya no está en ninguna parte.
    """
    client, mock_conn, _ = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.return_value = make_rule_record(
        rule_id=rid, name="Tutelas a Jurídica", assign_to_dept="Dirección Jurídica"
    )

    resp = await client.delete(f"/api/v1/workflow/rules/{rid}", headers=TENANT_HEADERS)

    assert resp.status_code == 204
    args = _asientos(mock_conn)[0].args
    assert args[_A_ACTION] == "workflow.regla_eliminada"
    assert args[_A_OBJECT_REF] == str(rid)
    assert args[_A_ACTOR] == str(USER_ID)
    regla = json.loads(args[_A_PAYLOAD])["regla"]
    assert regla["name"] == "Tutelas a Jurídica"
    assert regla["assign_to_dept"] == "Dirección Jurídica"


@pytest.mark.asyncio
async def test_borrar_una_regla_inexistente_no_asienta_nada(client_with_conn) -> None:
    """Control positivo: un 404 no es un acto, y no puede dejar rastro de uno.

    Sin esto, los tests de arriba pasarían igual con un servicio que asentara en
    todos los casos — y la cadena se llenaría de borrados que nunca ocurrieron.
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = None  # no existe

    resp = await client.delete(f"/api/v1/workflow/rules/{uuid.uuid4()}", headers=TENANT_HEADERS)

    assert resp.status_code == 404
    assert _asientos(mock_conn) == []


@pytest.mark.asyncio
async def test_un_patch_vacio_no_asienta_nada(client_with_conn) -> None:
    """Gemelo del anterior por el otro lado: sin cambios no hay acto.

    Un PATCH sin campos no muta nada; asentarlo llenaría la cadena de entradas
    que no describen ningún cambio, que es la forma de volverla ilegible.
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = make_rule_record()

    resp = await client.patch(
        f"/api/v1/workflow/rules/{uuid.uuid4()}", json={}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    assert _asientos(mock_conn) == []


@pytest.mark.asyncio
async def test_si_el_asiento_falla_la_regla_no_se_crea(client_with_conn, monkeypatch) -> None:
    """Atomicidad: la regla y su asiento son un solo acto.

    Es el criterio del resto del servicio y de ADR-010. Si la cadena inmutable no
    puede recibir el asiento, la regla no debe quedar activa enrutando
    correspondencia sin que conste quién la puso.
    """
    from app.services import rules_service

    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [1, 1]
    mock_conn.fetchrow.return_value = make_rule_record()

    async def _falla(*a, **kw):
        raise RuntimeError("cadena de auditoría caída")

    monkeypatch.setattr(rules_service.audit, "append", _falla)

    with pytest.raises(RuntimeError):
        await client.post(
            "/api/v1/workflow/rules", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS
        )
