# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests for batch API endpoints.

NOTA (deuda Fase 6): esta suite quedó incompleta desde su commit original —
referencia una fixture `test_client` que nunca existió y no envía las cabeceras
de tenant/usuario que el router exige. Necesita una fixture de integración con
BD real para ejercitar el procesamiento asíncrono del job. Se marca como skip
hasta reescribir la Batch API; el endpoint ya usa get_tenant_conn (ADR-012).
"""

from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skip(reason="Batch API tests pendientes de reescritura (deuda Fase 6)"),
]


async def test_create_batch_documents(test_client):
    """POST /api/v1/batch/documents creates a batch job."""
    client, _, _ = test_client

    payload = {
        "documents": [
            {
                "doc_type": "E",
                "subject": "Request 1",
                "sender_name": "Sender 1",
            },
            {
                "doc_type": "E",
                "subject": "Request 2",
                "sender_name": "Sender 2",
            },
            {
                "doc_type": "S",
                "subject": "Response 1",
                "sender_name": "Institution",
            },
        ]
    }

    response = await client.post(
        "/api/v1/batch/documents",
        json=payload,
    )

    assert response.status_code == 202, response.json()
    body = response.json()
    assert body["job_type"] == "documents"
    assert body["status"] == "pending"
    assert body["total_items"] == 3
    assert "job_id" in body
    assert "created_at" in body


async def test_batch_documents_too_few(test_client):
    """POST /api/v1/batch/documents rejects empty list."""
    client, _, _ = test_client

    payload = {"documents": []}

    response = await client.post(
        "/api/v1/batch/documents",
        json=payload,
    )

    assert response.status_code == 422


async def test_batch_documents_too_many(test_client):
    """POST /api/v1/batch/documents rejects 1000+ items."""
    client, _, _ = test_client

    payload = {
        "documents": [
            {
                "doc_type": "E",
                "subject": f"Document {i}",
            }
            for i in range(1001)
        ]
    }

    response = await client.post(
        "/api/v1/batch/documents",
        json=payload,
    )

    assert response.status_code == 422


async def test_get_batch_status(test_client):
    """GET /api/v1/batch/{job_id}/status returns job progress."""
    client, _, _ = test_client

    # Create batch job
    payload = {"documents": [{"doc_type": "E", "subject": f"Doc {i}"} for i in range(5)]}
    create_resp = await client.post("/api/v1/batch/documents", json=payload)
    job_id = create_resp.json()["job_id"]

    # Get status (should be pending initially)
    status_resp = await client.get(f"/api/v1/batch/{job_id}/status")
    assert status_resp.status_code == 200

    body = status_resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "pending"
    assert body["total_items"] == 5
    assert body["processed_items"] == 0
    assert body["failed_items"] == 0
    assert "items" in body
    assert len(body["items"]) == 5


async def test_get_batch_status_not_found(test_client):
    """GET /api/v1/batch/{job_id}/status returns 404 if not found."""
    client, _, _ = test_client

    fake_job_id = uuid4()
    response = await client.get(f"/api/v1/batch/{fake_job_id}/status")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
