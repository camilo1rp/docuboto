"""
E2E — Fase 4: document-service — radicación E/S/I, tracking number, anexos.
"""

import io
import pytest
import httpx

from tests.e2e.conftest import TENANT_HEADERS, E2E_TENANT_CODE


def _upload_file(http: httpx.Client, storage_url: str, content: bytes, filename: str) -> str:
    r = http.post(
        f"{storage_url}/api/v1/storage/upload",
        headers=TENANT_HEADERS,
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
    )
    assert r.status_code in (200, 201)
    return r.json()["id"]


def test_create_radicado_entrada(
    http: httpx.Client, document_url: str, storage_url: str
) -> None:
    """Crea un radicado de Entrada con un anexo."""
    file_id = _upload_file(http, storage_url, b"%PDF-1.4 solicitud", "solicitud.pdf")

    payload = {
        "doc_type": "E",
        "subject": "Solicitud de certificado E2E",
        "doc_class": "Derechos de Petición",
        "dest_dept": "Registro y Control",
        "sender_name": "María García E2E",
        "sender_entity": "Ciudadana",
        "pages": 2,
        "anexos": [
            {"file_id": file_id, "filename": "solicitud.pdf",
             "file_size": 18, "mime_type": "application/pdf"}
        ],
    }
    r = http.post(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        json=payload,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["doc_type"] == "E"
    assert body["tracking_number"].startswith(f"{body['year']}-{E2E_TENANT_CODE}-E-")
    assert len(body["tracking_number"].split("-")[-1]) == 6
    assert len(body["anexos"]) == 1
    assert body["anexos"][0]["file_id"] == file_id


def test_create_radicado_salida(
    http: httpx.Client, document_url: str
) -> None:
    """Crea un radicado de Salida."""
    r = http.post(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        json={
            "doc_type": "S",
            "subject": "Respuesta oficio E2E",
            "dest_dept": "Ministerio de Educación",
            "pages": 1,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert "-S-" in body["tracking_number"]


def test_tracking_numbers_sequential(
    http: httpx.Client, document_url: str
) -> None:
    """Dos radicados del mismo tipo tienen secuencias consecutivas."""
    def create():
        r = http.post(
            f"{document_url}/api/v1/documents",
            headers=TENANT_HEADERS,
            json={"doc_type": "I", "subject": "Interno E2E", "pages": 1},
        )
        assert r.status_code == 201
        return r.json()["sequence"]

    seq1 = create()
    seq2 = create()
    assert seq2 == seq1 + 1


def test_get_document_by_tracking(
    http: httpx.Client, document_url: str
) -> None:
    """Crea un radicado y lo consulta por tracking number."""
    r = http.post(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        json={"doc_type": "E", "subject": "Radicado para consulta E2E", "pages": 1},
    )
    tracking = r.json()["tracking_number"]

    r2 = http.get(
        f"{document_url}/api/v1/documents/by-tracking/{tracking}",
        headers=TENANT_HEADERS,
    )
    assert r2.status_code == 200
    assert r2.json()["tracking_number"] == tracking


def test_list_documents_with_filter(
    http: httpx.Client, document_url: str
) -> None:
    """Lista radicados filtrando por tipo."""
    r = http.get(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        params={"doc_type": "E", "page": 1, "size": 5},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert "x-total-count" in r.headers
    for doc in r.json():
        assert doc["doc_type"] == "E"


def test_update_document(
    http: httpx.Client, document_url: str
) -> None:
    """Crea y actualiza un radicado."""
    r = http.post(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        json={"doc_type": "E", "subject": "Para actualizar E2E", "pages": 1},
    )
    doc_id = r.json()["id"]
    original_tracking = r.json()["tracking_number"]

    r2 = http.patch(
        f"{document_url}/api/v1/documents/{doc_id}",
        headers=TENANT_HEADERS,
        json={"subject": "Actualizado E2E", "status": "distributed"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["subject"] == "Actualizado E2E"
    assert body["status"] == "distributed"
    # tracking_number es inmutable
    assert body["tracking_number"] == original_tracking
