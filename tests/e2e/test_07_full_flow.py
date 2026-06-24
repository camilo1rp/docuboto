"""
E2E — Fase 7: flujo completo de un radicado de Entrada.

Simula el ciclo de vida real:
  1. Subir un anexo al storage-service
  2. Registrar el radicado en document-service
  3. Asignar a una dependencia en workflow-service
  4. Crear expediente y vincular en archive-service
  5. Transferir a otra dependencia
  6. Completar el paso
"""

import io
import pytest
import httpx

from tests.e2e.conftest import TENANT_HEADERS, E2E_TENANT_CODE


def test_full_document_lifecycle(
    http: httpx.Client,
    storage_url: str,
    document_url: str,
    workflow_url: str,
    archive_url: str,
) -> None:
    """Flujo completo: upload → radicado → workflow → expediente → complete."""

    # 1. Subir anexo
    content = b"%PDF-1.4 documento de prueba completo E2E"
    r_upload = http.post(
        f"{storage_url}/api/v1/storage/upload",
        headers=TENANT_HEADERS,
        files={"file": ("carta_e2e.pdf", io.BytesIO(content), "application/pdf")},
    )
    assert r_upload.status_code in (200, 201), r_upload.text
    file_id = r_upload.json()["id"]

    # 2. Registrar radicado con el anexo
    r_doc = http.post(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        json={
            "doc_type": "E",
            "subject": "Derecho de petición — flujo completo E2E",
            "doc_class": "Derechos de Petición",
            "dest_dept": "Ventanilla Única",
            "sender_name": "Carlos López E2E",
            "sender_entity": "Ciudadano",
            "pages": 3,
            "anexos": [
                {"file_id": file_id, "filename": "carta_e2e.pdf",
                 "file_size": len(content), "mime_type": "application/pdf"}
            ],
        },
    )
    assert r_doc.status_code == 201, r_doc.text
    doc = r_doc.json()
    assert "-E-" in doc["tracking_number"]
    assert doc["status"] == "registered"
    assert len(doc["anexos"]) == 1

    # 3. Asignar a Ventanilla
    r_assign = http.post(
        f"{workflow_url}/api/v1/workflows/assign",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
            "to_dept": "Ventanilla Única",
            "action": "assign",
            "notes": "Recibido en ventanilla",
        },
    )
    assert r_assign.status_code == 201, r_assign.text
    step1_id = r_assign.json()["id"]
    assert r_assign.json()["step_number"] == 1

    # 4. Crear expediente y vincular el radicado
    r_exp = http.post(
        f"{archive_url}/api/v1/expedientes",
        headers=TENANT_HEADERS,
        json={"name": "Expediente flujo completo E2E"},
    )
    assert r_exp.status_code == 201
    exp_id = r_exp.json()["id"]

    r_link = http.post(
        f"{archive_url}/api/v1/expedientes/{exp_id}/radicados",
        headers=TENANT_HEADERS,
        json={"radicado_id": doc["id"], "tracking_number": doc["tracking_number"]},
    )
    assert r_link.status_code == 201

    # 5. Transferir a Dirección
    r_transfer = http.post(
        f"{workflow_url}/api/v1/workflows/transfer",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
            "from_dept": "Ventanilla Única",
            "to_dept": "Dirección General",
            "action": "transfer",
            "notes": "Requiere revisión directiva",
        },
    )
    assert r_transfer.status_code == 201
    step2_id = r_transfer.json()["id"]
    assert r_transfer.json()["step_number"] == 2

    # 6. Verificar historial — 2 pasos, el primero completed
    r_hist = http.get(
        f"{workflow_url}/api/v1/workflows/{doc['id']}/history",
        headers=TENANT_HEADERS,
    )
    assert r_hist.status_code == 200
    steps = r_hist.json()["steps"]
    assert len(steps) == 2
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "pending"

    # 7. Completar el paso en Dirección
    r_complete = http.patch(
        f"{workflow_url}/api/v1/workflows/{step2_id}/complete",
        headers=TENANT_HEADERS,
        json={"notes": "Resuelto favorablemente"},
    )
    assert r_complete.status_code == 200
    assert r_complete.json()["status"] == "completed"

    # 8. Cerrar el expediente
    r_close = http.patch(
        f"{archive_url}/api/v1/expedientes/{exp_id}",
        headers=TENANT_HEADERS,
        json={"status": "closed"},
    )
    assert r_close.status_code == 200
    assert r_close.json()["status"] == "closed"

    # Verificación final: el radicado sigue accesible por tracking number
    r_final = http.get(
        f"{document_url}/api/v1/documents/by-tracking/{doc['tracking_number']}",
        headers=TENANT_HEADERS,
    )
    assert r_final.status_code == 200
    assert r_final.json()["tracking_number"] == doc["tracking_number"]
