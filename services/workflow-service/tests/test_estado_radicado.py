# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-028: el flujo mueve el estado del radicado — CABLEADO (unitario, mock).

La lógica SQL y la atomicidad real contra Postgres están en `tests/integration/`.
Aquí se prueba: la función pura (tabla completa), qué actos llaman a la
sincronización y qué hacen con su respuesta (409 si es terminal), la tenencia de
`marcar_leido`, `finalizar` y que ninguna LECTURA sincroniza.
"""

from __future__ import annotations

import itertools
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.services.estado_radicado import ESTADOS_TERMINALES, derivar_estado_radicado
from tests.conftest import (
    RADICADO_ID,
    STEP_ID,
    TENANT_HEADERS,
    USER_ID,
    make_event_record,
    make_step_record,
)

Cliente = tuple[AsyncClient, AsyncMock, MagicMock]

_ASSIGN = {
    "radicado_id": str(RADICADO_ID),
    "tracking_number": "x",
    "to_dept": "Dirección Jurídica",
    "action": "assign",
}


# ---------------------------------------------------------------------------
# Función pura: tabla completa
# ---------------------------------------------------------------------------


def _esperado(actual: str, activo: str | None, tiene: bool, finalizar: bool) -> str:
    """Oráculo escrito aparte, a propósito, como tabla de decisión (no como
    espejo línea a línea de la implementación)."""
    if actual in ("closed", "anulado"):
        return actual
    if finalizar:
        return "closed"
    if activo == "pending":
        return "distributed"
    if activo == "in_progress":
        return "in_progress"
    if actual == "registered" and tiene:
        return "distributed"
    return actual


@pytest.mark.parametrize(
    ("actual", "activo", "tiene", "finalizar"),
    list(
        itertools.product(
            ["registered", "distributed", "in_progress", "closed", "anulado"],
            [None, "pending", "in_progress"],
            [False, True],
            [False, True],
        )
    ),
)
def test_derivar_estado_tabla_completa(
    actual: str, activo: str | None, tiene: bool, finalizar: bool
) -> None:
    # Un paso activo implica que hay pasos: las combinaciones incoherentes se
    # prueban igual (la función no debe depender de esa coherencia).
    assert (
        derivar_estado_radicado(actual, activo, tiene_pasos=tiene, finalizar=finalizar)
        == _esperado(actual, activo, tiene, finalizar)
    )


@pytest.mark.parametrize("terminal", sorted(ESTADOS_TERMINALES))
def test_terminal_no_lo_mueve_nada(terminal: str) -> None:
    for activo in (None, "pending", "in_progress"):
        for fin in (False, True):
            assert (
                derivar_estado_radicado(terminal, activo, tiene_pasos=True, finalizar=fin)
                == terminal
            )


def test_casos_nominales() -> None:
    d = derivar_estado_radicado
    assert d("registered", "pending", tiene_pasos=True) == "distributed"
    assert d("distributed", "in_progress", tiene_pasos=True) == "in_progress"
    assert d("in_progress", "pending", tiene_pasos=True) == "distributed"  # devolver/rollback
    # Completar sin marcar leído: queda distributed (declarado).
    assert d("distributed", None, tiene_pasos=True) == "distributed"
    assert d("in_progress", None, tiene_pasos=True) == "in_progress"
    # Sin reparto: sigue registered.
    assert d("registered", None, tiene_pasos=False) == "registered"
    assert d("in_progress", None, tiene_pasos=True, finalizar=True) == "closed"


# ---------------------------------------------------------------------------
# 409 en el router cuando el radicado es terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["closed", "anulado"])
async def test_assign_sobre_radicado_terminal_409(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock, terminal: str
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(step_id=STEP_ID)
    _sin_sincronizacion_de_estado.return_value = (terminal, terminal)

    resp = await client.post("/api/v1/workflows/assign", json=_ASSIGN, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "radicado_terminal",
        "detail": "El radicado está cerrado o anulado y no admite actos de trámite",
        "status": 409,
    }
    # El 409 sale ANTES del asiento y del evento: nada se publica ni se audita.
    assert not any(
        "INSERT INTO public.audit_log" in c.args[0] for c in mock_conn.execute.await_args_list
    )
    mock_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_sobre_radicado_terminal_409(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(
        step_id=STEP_ID, step_status="completed", assigned_to=USER_ID
    )
    _sin_sincronizacion_de_estado.return_value = ("anulado", "anulado")

    resp = await client.patch(
        f"/api/v1/workflows/{STEP_ID}/complete", json={}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "radicado_terminal"


@pytest.mark.asyncio
async def test_complete_paso_inexistente_sigue_siendo_404_step_not_found(
    client_with_conn: Cliente,
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_by_id -> None

    resp = await client.patch(
        f"/api/v1/workflows/{STEP_ID}/complete", json={}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "step_not_found"


@pytest.mark.asyncio
async def test_cambio_de_estado_va_en_el_asiento_y_se_emite_tras_commit(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(step_id=STEP_ID)
    _sin_sincronizacion_de_estado.return_value = ("registered", "distributed")

    resp = await client.post("/api/v1/workflows/assign", json=_ASSIGN, headers=TENANT_HEADERS)
    assert resp.status_code == 201

    audit_calls = [
        c for c in mock_conn.execute.await_args_list if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    payload = json.loads(audit_calls[0].args[8])
    assert payload["estado_radicado"] == {"antes": "registered", "despues": "distributed"}

    tipos = [c.args[1]["event_type"] for c in mock_redis.xadd.call_args_list]
    assert tipos == ["workflow.step.created", "workflow.radicado.estado_cambiado"]
    evt = mock_redis.xadd.call_args_list[1].args[1]
    assert evt["dedup_key"].startswith(f"{RADICADO_ID}:distributed:")
    assert json.loads(evt["payload"])["despues"] == "distributed"


@pytest.mark.asyncio
async def test_sin_cambio_no_hay_evento_ni_clave_en_el_asiento(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(step_id=STEP_ID)
    _sin_sincronizacion_de_estado.return_value = ("distributed", "distributed")

    resp = await client.post("/api/v1/workflows/assign", json=_ASSIGN, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    audit_calls = [
        c for c in mock_conn.execute.await_args_list if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "estado_radicado" not in json.loads(audit_calls[0].args[8])
    assert [c.args[1]["event_type"] for c in mock_redis.xadd.call_args_list] == [
        "workflow.step.created"
    ]


# ---------------------------------------------------------------------------
# Transacciones: tenencia de marcar_leido + sincronización solo con target
# ---------------------------------------------------------------------------


def _tt(code: str, target: str | None, *, holder: bool) -> dict:
    return {
        "code": code,
        "nombre": code,
        "descripcion": None,
        "requires_permission": "PERM_TRAMITAR",
        "target_step_status": target,
        "requires_holder": holder,
        "activo": True,
    }


@pytest.mark.asyncio
async def test_marcar_leido_sin_ser_poseedor_404_neutro(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, _ = client_with_conn
    latest = make_step_record(step_status="pending")
    mock_conn.fetchrow.side_effect = [_tt("marcar_leido", "in_progress", holder=True), latest]
    # 1) RBAC concede PERM_TRAMITAR; 2) es_poseedor -> nadie.
    mock_conn.fetchval.side_effect = [1, None]

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "marcar_leido"},
        headers=TENANT_HEADERS,
    )

    # Mismo 404 que "no hay paso activo": no distingue "no es tuyo".
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "no_active_step"
    assert not any("INSERT INTO flow_events" in str(c) for c in mock_conn.fetchrow.await_args_list)
    _sin_sincronizacion_de_estado.assert_not_awaited()
    assert not any(
        "INSERT INTO public.audit_log" in c.args[0] for c in mock_conn.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_marcar_leido_del_poseedor_sincroniza(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, _ = client_with_conn
    latest = make_step_record(step_status="pending")
    event = make_event_record(tipo_tx="marcar_leido")
    mock_conn.fetchrow.side_effect = [_tt("marcar_leido", "in_progress", holder=True), latest, event]
    _sin_sincronizacion_de_estado.return_value = ("distributed", "in_progress")

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "marcar_leido"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    _sin_sincronizacion_de_estado.assert_awaited_once()


@pytest.mark.asyncio
async def test_informar_no_sincroniza_ni_exige_tenencia(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    """informar/agendar siguen permitidas (también sobre un terminal): target
    NULL => no se sincroniza, y sin `requires_holder` no hay comprobación."""
    client, mock_conn, _ = client_with_conn
    latest = make_step_record(step_status="pending")
    event = make_event_record(tipo_tx="informar")
    mock_conn.fetchrow.side_effect = [_tt("informar", None, holder=False), latest, event]

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "informar"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    _sin_sincronizacion_de_estado.assert_not_awaited()


@pytest.mark.asyncio
async def test_transaccion_con_target_sobre_terminal_409(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, _ = client_with_conn
    latest = make_step_record(step_status="pending")
    event = make_event_record(tipo_tx="anular")
    mock_conn.fetchrow.side_effect = [_tt("anular", "cancelled", holder=False), latest, event]
    _sin_sincronizacion_de_estado.return_value = ("closed", "closed")

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "anular"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "radicado_terminal"


# ---------------------------------------------------------------------------
# finalizar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalizar_exige_comentario(client_with_conn: Cliente) -> None:
    client, mock_conn, _ = client_with_conn
    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/finalizar", json={}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/finalizar",
        json={"comentario": "abc"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_finalizar_no_poseedor_404_neutro(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(step_status="pending")
    mock_conn.fetchval.side_effect = [1, None]  # RBAC; es_poseedor

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/finalizar",
        json={"comentario": "Trámite concluido", "motivo_cierre": "respondido"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "no_active_step"
    _sin_sincronizacion_de_estado.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalizar_del_poseedor_cierra(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    activo = make_step_record(step_id=STEP_ID, step_status="in_progress", assigned_to=USER_ID)
    cerrado = make_step_record(step_id=STEP_ID, step_status="completed", assigned_to=USER_ID)
    # get_active_step_for_update, UPDATE ... RETURNING del apply_finalizar
    mock_conn.fetchrow.side_effect = [activo, cerrado]
    _sin_sincronizacion_de_estado.return_value = ("in_progress", "closed")

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/finalizar",
        json={"comentario": "Trámite concluido", "motivo_cierre": "respondido"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    _sin_sincronizacion_de_estado.assert_awaited_once_with(RADICADO_ID, finalizar=True)

    ev = [c for c in mock_conn.execute.await_args_list if "INSERT INTO flow_events" in c.args[0]]
    assert len(ev) == 1 and ev[0].args[3] == "finalizar"
    au = [
        c for c in mock_conn.execute.await_args_list if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert au[0].args[5] == "workflow.finalizar"
    assert json.loads(au[0].args[8])["estado_radicado"] == {
        "antes": "in_progress",
        "despues": "closed",
    }


@pytest.mark.asyncio
async def test_finalizar_sobre_anulado_409(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock
) -> None:
    client, mock_conn, _ = client_with_conn
    activo = make_step_record(step_id=STEP_ID, step_status="pending", assigned_to=USER_ID)
    mock_conn.fetchrow.side_effect = [activo, activo]
    _sin_sincronizacion_de_estado.return_value = ("anulado", "anulado")

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/finalizar",
        json={"comentario": "Trámite concluido", "motivo_cierre": "respondido"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Las LECTURAS nunca sincronizan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ruta",
    [
        f"/api/v1/workflows/{RADICADO_ID}/current",
        f"/api/v1/workflows/{RADICADO_ID}/history",
        f"/api/v1/workflows/{RADICADO_ID}/events",
        "/api/v1/workflows/inbox",
        "/api/v1/workflows/pending",
        "/api/v1/workflows/transaction-types",
    ],
)
async def test_los_get_nunca_sincronizan(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock, ruta: str
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record()
    if ruta.endswith("/events"):
        mock_conn.fetch.return_value = [make_event_record()]
    elif ruta.endswith("/transaction-types"):
        mock_conn.fetch.return_value = []
    else:
        mock_conn.fetch.return_value = [make_step_record()]
    mock_conn.fetchval.return_value = 0

    resp = await client.get(ruta, headers=TENANT_HEADERS)

    # 200: la lectura SE EJECUTÓ (no salió por un error previo que dejara el
    # assert de abajo trivialmente cierto).
    assert resp.status_code == 200

    _sin_sincronizacion_de_estado.assert_not_awaited()
    assert not any(
        "UPDATE radicados" in str(c.args[0])
        for c in (*mock_conn.execute.await_args_list, *mock_conn.fetchrow.await_args_list)
    )


# ---------------------------------------------------------------------------
# Cableado de devolver / rollback: el fixture autouse vuelve no-op la sincronización,
# así que cada acto debe probar AQUÍ que la llama y qué hace con su respuesta
# (su efecto real en BD lo prueban los tests de tests/integration/).
# ---------------------------------------------------------------------------

_DEVOLVER = {"to_dept": "Ventanilla", "causal": "DEV-DEP", "comentario": "No competencia"}
_MOTIVO = "Se asignó a la dependencia incorrecta por error de digitación."


def _audits(mock_conn: AsyncMock) -> list:
    return [c for c in mock_conn.execute.await_args_list if "INSERT INTO public.audit_log" in c.args[0]]


def _preparar(mock_conn: AsyncMock, acto: str) -> str:
    current = make_step_record(step_number=2, step_status="pending", to_dept="B", assigned_to=USER_ID)
    if acto == "devolver":
        mock_conn.fetchrow.side_effect = [current, make_step_record(step_number=3, to_dept="Ventanilla")]
        return f"/api/v1/workflows/{RADICADO_ID}/devolver"
    previous = make_step_record(step_number=1, step_status="completed", to_dept="A")
    mock_conn.fetchrow.side_effect = [current, previous, make_step_record(step_number=1, to_dept="A")]
    return f"/api/v1/workflows/{RADICADO_ID}/rollback"


@pytest.mark.asyncio
@pytest.mark.parametrize("acto", ["devolver", "rollback"])
async def test_devolver_y_rollback_sincronizan_y_dejan_el_cambio_en_el_asiento(
    client_with_conn: Cliente, _sin_sincronizacion_de_estado: AsyncMock, acto: str
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    url = _preparar(mock_conn, acto)
    _sin_sincronizacion_de_estado.return_value = ("in_progress", "distributed")

    resp = await client.post(
        url, json=_DEVOLVER if acto == "devolver" else {"motivo": _MOTIVO}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    _sin_sincronizacion_de_estado.assert_awaited_once()
    (audit_call,) = _audits(mock_conn)
    assert json.loads(audit_call.args[8])["estado_radicado"] == {
        "antes": "in_progress",
        "despues": "distributed",
    }
    tipos = [c.args[1]["event_type"] for c in mock_redis.xadd.call_args_list]
    assert tipos == ["workflow.radicado.estado_cambiado"]


@pytest.mark.asyncio
@pytest.mark.parametrize("acto", ["devolver", "rollback"])
@pytest.mark.parametrize("terminal", ["closed", "anulado"])
async def test_devolver_y_rollback_sobre_terminal_409_sin_asiento_ni_evento(
    client_with_conn: Cliente,
    _sin_sincronizacion_de_estado: AsyncMock,
    acto: str,
    terminal: str,
) -> None:
    client, mock_conn, mock_redis = client_with_conn
    url = _preparar(mock_conn, acto)
    _sin_sincronizacion_de_estado.return_value = (terminal, terminal)

    resp = await client.post(
        url, json=_DEVOLVER if acto == "devolver" else {"motivo": _MOTIVO}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "radicado_terminal"
    assert _audits(mock_conn) == []
    mock_redis.xadd.assert_not_awaited()
