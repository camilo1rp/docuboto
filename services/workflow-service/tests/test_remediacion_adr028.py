# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Remediación de auditoría de ADR-028 — el CABLEADO, en el carril mockeado.

La lógica SQL (tenencia, clearance en el `WHERE`, cerrojos, migraciones) la prueba
`tests/integration/` contra Postgres real; aquí se fija lo que un mock sí puede
afirmar: qué gate exige cada ruta, con qué argumentos se llama a cada lectura y que
el reparto automático sigue siendo un actor de sistema.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.repositories.workflow_repository import Tenencia
from app.schemas.workflow import FlowStepCreate
from app.services.distribution import SYSTEM_USER, auto_distribute
from app.services.workflow_service import WorkflowService
from tests.conftest import (
    RADICADO_ID,
    TENANT_HEADERS,
    TENANT_SLUG,
    TRACKING_NUMBER,
    USER_ID,
    make_step_record,
)

Cliente = tuple[AsyncClient, AsyncMock, MagicMock]
_REPO = "app.services.workflow_service.WorkflowRepository"


def _payload() -> FlowStepCreate:
    return FlowStepCreate(
        radicado_id=RADICADO_ID,
        tracking_number="ignorado",
        to_dept_code=4,
        action="assign",
    )


# --- 3. finalizar exige nivel de ESCRITURA fuerte ---------------------------------


@pytest.mark.asyncio
async def test_finalizar_exige_nivel_3_no_el_de_lectura(client_with_conn: Cliente) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # el RBAC no concede nada

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/finalizar",
        json={"comentario": "Trámite concluido", "motivo_cierre": "respondido"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    # El gate preguntó por PERM_TRAMITAR con min_crud=3 (args: sql, user, perm, nivel).
    rbac = next(c for c in mock_conn.fetchval.await_args_list if "auth_group_permissions" in c.args[0])
    assert rbac.args[2:] == ("PERM_TRAMITAR", 3)


# --- 1. assign/transfer: cableado de clearance y tenencia -----------------------------


@pytest.mark.asyncio
async def test_assign_resuelve_el_clearance_del_actor_y_lo_pasa_a_la_lectura_del_radicado() -> None:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    svc = WorkflowService(conn, TENANT_SLUG)
    with (
        patch(f"{_REPO}.clearance_para_acto", new=AsyncMock(return_value=2)) as clearance,
        patch(f"{_REPO}.get_radicado_tracking_number", new=AsyncMock(return_value=None)) as lectura,
        pytest.raises(HTTPException) as exc,
    ):
        await svc.assign(_payload(), USER_ID)

    clearance.assert_awaited_once_with(USER_ID)
    # El nivel viaja a la lectura (donde va en el `WHERE`), no a una comprobación aparte.
    lectura.assert_awaited_once_with(RADICADO_ID, max_clearance=2)
    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "radicado_not_found"


@pytest.mark.asyncio
async def test_assign_de_un_no_poseedor_con_paso_activo_da_404_neutro_y_no_escribe() -> None:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    svc = WorkflowService(conn, TENANT_SLUG)
    with (
        patch(f"{_REPO}.get_radicado_tracking_number", new=AsyncMock(return_value=TRACKING_NUMBER)),
        patch(f"{_REPO}.tenencia", new=AsyncMock(return_value=Tenencia(hay_activo=True, poseedor=False))),
        patch(f"{_REPO}.create", new=AsyncMock()) as crear,
        pytest.raises(HTTPException) as exc,
    ):
        await svc.assign(_payload(), USER_ID)

    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "no_active_step"
    crear.assert_not_awaited()


@pytest.mark.asyncio
async def test_assign_de_sistema_no_resuelve_clearance_ni_tenencia_y_lee_sin_restriccion() -> None:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchval.return_value = 1
    conn.fetchrow.return_value = make_step_record(action="assign")
    svc = WorkflowService(conn, TENANT_SLUG)
    with (
        patch(f"{_REPO}.clearance_para_acto", new=AsyncMock()) as clearance,
        patch(f"{_REPO}.tenencia", new=AsyncMock()) as tenencia,
        patch(f"{_REPO}.get_radicado_tracking_number", new=AsyncMock(return_value=TRACKING_NUMBER)) as lectura,
    ):
        await svc.assign(_payload(), SYSTEM_USER, sistema=True)

    clearance.assert_not_awaited()
    tenencia.assert_not_awaited()
    assert lectura.await_args.kwargs["max_clearance"] == 3


@pytest.mark.asyncio
async def test_el_reparto_automatico_se_declara_actor_de_sistema() -> None:
    """`auto_distribute` es el ÚNICO llamante de `sistema=True`: sin él, el reparto
    de un radicado reservado fallaría (el actor de sistema no tiene clearance)."""
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "name": "Regla",
            "priority": 1,
            "assign_to_dept": "Dirección Jurídica",
            "assign_notes": None,
            "is_active": True,
        }
    ]
    with (
        patch("app.services.distribution.WorkflowRulesService.evaluate", new=AsyncMock()) as evaluar,
        patch("app.services.distribution.WorkflowService.assign", new=AsyncMock()) as asignar,
    ):
        evaluar.return_value = MagicMock(
            matched=True,
            assign_to_dept="Dirección Jurídica",
            assign_to_dept_code=4,
            assign_notes=None,
            rule_id=None,
            rule_name="r",
        )
        await auto_distribute(
            conn,
            TENANT_SLUG,
            {"id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER, "doc_type": "E"},
        )
    assert asignar.await_args.kwargs["sistema"] is True
    assert asignar.await_args.kwargs["assigned_by"] == SYSTEM_USER


@pytest.mark.asyncio
async def test_ninguna_ruta_http_pasa_sistema_true(client_with_conn: Cliente) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = make_step_record(action="assign")
    with patch("app.routers.workflow.WorkflowService.assign", new=AsyncMock()) as asignar:
        asignar.return_value = MagicMock()
        try:
            await client.post(
                "/api/v1/workflows/assign",
                json={
                    "radicado_id": str(RADICADO_ID),
                    "tracking_number": "x",
                    "to_dept_code": 4,
                    "action": "assign",
                },
                headers=TENANT_HEADERS,
            )
        except Exception:  # noqa: BLE001 — la respuesta del mock no valida; solo importa la llamada
            pass
    assert asignar.await_args is not None  # el control no es vacuo: la ruta SÍ llamó
    assert "sistema" not in asignar.await_args.kwargs


# --- 2. cualquier transacción que mueve el paso exige tenencia ----------------------------


@pytest.mark.asyncio
async def test_una_transaccion_que_mueve_el_paso_exige_tenencia_aunque_no_lo_declare(
    client_with_conn: Cliente,
) -> None:
    client, mock_conn, _ = client_with_conn
    tipo: dict[str, Any] = {
        "code": "cierra_paso",
        "nombre": "x",
        "descripcion": None,
        "requires_permission": "PERM_TRAMITAR",
        "target_step_status": "completed",
        "requires_holder": False,  # la fila NO lo declara
        "activo": True,
    }
    mock_conn.fetchrow.side_effect = [tipo, make_step_record(step_status="pending")]
    mock_conn.fetchval.side_effect = [1, None]  # RBAC; es_poseedor -> no

    resp = await client.post(
        f"/api/v1/workflows/{RADICADO_ID}/transactions",
        json={"tipo_tx": "cierra_paso"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404 and resp.json()["detail"]["error"] == "no_active_step"


# --- 13. `_cambios` se vacía al inicio de cada acto ------------------------------------------


@pytest.mark.asyncio
async def test_los_cambios_de_un_acto_revertido_no_sobreviven_al_siguiente_acto() -> None:
    from app.repositories.workflow_repository import EstadoCambio

    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    svc = WorkflowService(conn, TENANT_SLUG)
    svc._cambios = [EstadoCambio(RADICADO_ID, TRACKING_NUMBER, "registered", "distributed", None)]
    with (
        patch(f"{_REPO}.get_radicado_tracking_number", new=AsyncMock(return_value=None)),
        pytest.raises(HTTPException),
    ):
        await svc.assign(_payload(), USER_ID)
    # El acto fallido empezó limpiando lo que dejó el anterior.
    assert svc._cambios == []
