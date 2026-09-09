# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
E2E — Fase 3: storage-service — upload, deduplicación, presigned URL, delete.
"""

import io
import pytest
import httpx

from tests.e2e.conftest import TENANT_HEADERS


def test_upload_file(http: httpx.Client, storage_url: str) -> None:
    """Sube un archivo de texto y verifica la respuesta."""
    content = b"Contenido del archivo E2E de prueba"
    r = http.post(
        f"{storage_url}/api/v1/storage/upload",
        headers=TENANT_HEADERS,
        files={"file": ("e2e_test.txt", io.BytesIO(content), "text/plain")},
    )
    assert r.status_code in (200, 201)
    body = r.json()
    assert "id" in body
    assert body["filename"] == "e2e_test.txt"
    assert len(body["sha256"]) == 64


def test_upload_deduplication(http: httpx.Client, storage_url: str) -> None:
    """Subir el mismo contenido dos veces retorna el mismo file_id."""
    content = b"Contenido deduplicado E2E"
    kwargs = dict(
        headers=TENANT_HEADERS,
        files={"file": ("dedup.txt", io.BytesIO(content), "text/plain")},
    )
    r1 = http.post(f"{storage_url}/api/v1/storage/upload", **kwargs)
    kwargs["files"] = {"file": ("dedup.txt", io.BytesIO(content), "text/plain")}
    r2 = http.post(f"{storage_url}/api/v1/storage/upload", **kwargs)

    assert r1.status_code in (200, 201)
    assert r2.status_code in (200, 201)
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["sha256"] == r2.json()["sha256"]


def test_get_file_metadata(http: httpx.Client, storage_url: str) -> None:
    """Sube un archivo y verifica que se puede consultar su metadata."""
    content = b"Archivo para metadata E2E"
    r = http.post(
        f"{storage_url}/api/v1/storage/upload",
        headers=TENANT_HEADERS,
        files={"file": ("meta.txt", io.BytesIO(content), "text/plain")},
    )
    assert r.status_code in (200, 201)
    file_id = r.json()["id"]

    r2 = http.get(
        f"{storage_url}/api/v1/storage/files/{file_id}",
        headers=TENANT_HEADERS,
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == file_id


def test_get_presigned_url(http: httpx.Client, storage_url: str) -> None:
    """Obtiene URL pre-firmada de un archivo subido."""
    content = b"Archivo para presigned URL"
    r = http.post(
        f"{storage_url}/api/v1/storage/upload",
        headers=TENANT_HEADERS,
        files={"file": ("presign.txt", io.BytesIO(content), "text/plain")},
    )
    file_id = r.json()["id"]

    r2 = http.get(
        f"{storage_url}/api/v1/storage/files/{file_id}/download",
        headers=TENANT_HEADERS,
    )
    assert r2.status_code == 200
    body = r2.json()
    assert "url" in body
    assert body["expires_in"] == 3600


def test_file_not_found(http: httpx.Client, storage_url: str) -> None:
    fake_id = "00000000-0000-0000-0000-000000000099"
    r = http.get(
        f"{storage_url}/api/v1/storage/files/{fake_id}",
        headers=TENANT_HEADERS,
    )
    assert r.status_code == 404
