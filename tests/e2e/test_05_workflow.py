"""
E2E — Fase 5: workflow-service — asignación, transferencia, historial.
"""

import uuid
import pytest
import httpx

from tests.e2e.conftest import TENANT_HEADERS, E2E_TENANT_CODE, E2E_USER_ID


def _create_radicado(http: httpx.Client, document_url: str) -> dict:
    r = http.post(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        json={"doc_type": "E", "subject": "Radicado para workflow E2E", "pages": 1},
    )
    assert r.status_code == 201
    return r.json()


def test_assign_radicado(
    http: httpx.Client, workflow_url: str, document_url: str
) -> None:
    """Asigna un radicado a una dependencia."""
    doc = _create_radicado(http, document_url)

    r = http.post(
        f"{workflow_url}/api/v1/workflows/assign",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
            "to_dept": "Ventanilla E2E",
            "action": "assign",
            "notes": "Asignación inicial E2E",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["to_dept"] == "Ventanilla E2E"
    assert body["step_number"] == 1
    assert body["status"] == "pending"


def test_transfer_radicado(
    http: httpx.Client, workflow_url: str, document_url: str
) -> None:
    """Asigna y luego transfiere un radicado."""
    doc = _create_radicado(http, document_url)

    http.post(
        f"{workflow_url}/api/v1/workflows/assign",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
            "to_dept": "Ventanilla E2E",
            "action": "assign",
        },
    )

    r = http.post(
        f"{workflow_url}/api/v1/workflows/transfer",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
            "from_dept": "Ventanilla E2E",
            "to_dept": "Dirección Jurídica E2E",
            "action": "transfer",
            "notes": "Requiere concepto jurídico",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["step_number"] == 2
    assert r.json()["to_dept"] == "Dirección Jurídica E2E"


def test_flow_history(
    http: httpx.Client, workflow_url: str, document_url: str
) -> None:
    """El historial refleja todos los pasos en orden."""
    doc = _create_radicado(http, document_url)

    for dept in ["Ventanilla E2E", "Jurídica E2E", "Dirección E2E"]:
        http.post(
            f"{workflow_url}/api/v1/workflows/assign",
            headers=TENANT_HEADERS,
            json={
                "radicado_id": doc["id"],
                "tracking_number": doc["tracking_number"],
                "to_dept": dept,
                "action": "assign",
            },
        )

    r = http.get(
        f"{workflow_url}/api/v1/workflows/{doc['id']}/history",
        headers=TENANT_HEADERS,
    )
    assert r.status_code == 200
    steps = r.json()["steps"]
    assert len(steps) == 3
    assert [s["step_number"] for s in steps] == [1, 2, 3]


def test_complete_step(
    http: httpx.Client, workflow_url: str, document_url: str
) -> None:
    """Completar un paso lo marca como completed."""
    doc = _create_radicado(http, document_url)

    r_assign = http.post(
        f"{workflow_url}/api/v1/workflows/assign",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
            "to_dept": "Para completar E2E",
            "action": "assign",
        },
    )
    step_id = r_assign.json()["id"]

    r = http.patch(
        f"{workflow_url}/api/v1/workflows/{step_id}/complete",
        headers=TENANT_HEADERS,
        json={"notes": "Procesado correctamente"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert r.json()["completed_at"] is not None


def test_pending_documents(
    http: httpx.Client, workflow_url: str, document_url: str
) -> None:
    """Los radicados asignados aparecen en la lista de pendientes."""
    doc = _create_radicado(http, document_url)
    dept = "Departamento Pendientes E2E"

    http.post(
        f"{workflow_url}/api/v1/workflows/assign",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
            "to_dept": dept,
            "action": "assign",
        },
    )

    r = http.get(
        f"{workflow_url}/api/v1/workflows/pending",
        headers=TENANT_HEADERS,
        params={"dept": dept},
    )
    assert r.status_code == 200
    assert int(r.headers.get("x-total-count", 0)) >= 1
