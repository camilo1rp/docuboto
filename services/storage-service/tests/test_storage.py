# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for storage-service endpoints.

DB and MinIO are fully mocked — no real infrastructure required.

Repository call sequence for UPLOAD (new file):
  1. execute  — SET search_path (find_by_sha256)
  2. fetchrow — SELECT sha256 → None (no duplicate)
  3. [MinIO]  bucket_exists → True
  4. [MinIO]  put_object
  5. execute  — SET search_path (create)
  6. fetchrow — INSERT INTO files RETURNING ...

Repository call sequence for UPLOAD (duplicate):
  1. execute  — SET search_path (find_by_sha256)
  2. fetchrow — SELECT sha256 → existing record (deduplication short-circuits)

Repository call sequence for GET metadata / presigned URL:
  1. execute  — SET search_path (get_by_id)
  2. fetchrow — SELECT file

Repository call sequence for DELETE:
  1. execute  — SET search_path (get_by_id in delete method)
  2. fetchrow — SELECT file
  3. [MinIO]  remove_object
  4. execute  — SET search_path (delete)
  5. execute  — DELETE FROM files
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from tests.conftest import (
    FILE_ID,
    TENANT_HEADERS,
    make_file_record,
)

_FILE_CONTENT = b"PDF content here"
_FILENAME = "carta.pdf"
_MIME_TYPE = "application/pdf"


# ---------------------------------------------------------------------------
# POST /api/v1/storage/upload — success (new file)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_success(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    record = make_file_record(file_id=FILE_ID)

    # find_by_sha256 returns None (no duplicate), then create returns the record
    mock_conn.fetchrow.side_effect = [None, record]

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["filename"] == _FILENAME
    assert body["mime_type"] == _MIME_TYPE
    assert body["file_size"] == record["file_size"]
    assert len(body["sha256"]) == 64
    assert "uploaded_at" in body
    mock_minio.put_object.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/v1/storage/upload — deduplication (same SHA-256)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_deduplication(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Uploading the same content twice returns the existing record with 200."""
    client, mock_conn, mock_minio = client_with_conn
    existing = make_file_record(file_id=FILE_ID)

    # find_by_sha256 returns the existing record → short-circuit, no MinIO call
    mock_conn.fetchrow.return_value = existing

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(existing["id"])
    mock_minio.put_object.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /api/v1/storage/upload — missing X-Tenant-Slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_missing_tenant_header(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        # no X-Tenant-Slug header
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id} — metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_metadata(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.return_value = record

    response = await client.get(
        f"/api/v1/storage/files/{FILE_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(FILE_ID)
    assert body["filename"] == record["filename"]
    assert body["sha256"] == record["sha256"]


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id} — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/storage/files/{uuid.uuid4()}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id}/download — presigned URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_presigned_url(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.return_value = record
    mock_minio.presigned_get_object.return_value = "http://minio/presigned-url"

    response = await client.get(
        f"/api/v1/storage/files/{FILE_ID}/download",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "http://minio/presigned-url"
    assert body["expires_in"] == 3600
    mock_minio.presigned_get_object.assert_awaited_once()


# ---------------------------------------------------------------------------
# DELETE /api/v1/storage/files/{file_id} — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_file(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    record = make_file_record(file_id=FILE_ID)

    # get_by_id returns the record, then DELETE returns "DELETE 1"
    mock_conn.fetchrow.return_value = record
    mock_conn.execute.side_effect = [None, None, "DELETE 1"]

    response = await client.delete(
        f"/api/v1/storage/files/{FILE_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 204
    mock_minio.remove_object.assert_awaited_once()


# ---------------------------------------------------------------------------
# DELETE /api/v1/storage/files/{file_id} — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_file_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.delete(
        f"/api/v1/storage/files/{uuid.uuid4()}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# POST /upload — formato no admitido (RF-DIG-04)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_unsupported_format_is_415(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": ("malware.exe", b"MZ...", "application/x-msdownload")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "unsupported_media_type"
    mock_minio.put_object.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /files/{id}/verify — reverificación de integridad (RF-DIG-02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_integrity_ok(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    import hashlib

    client, mock_conn, mock_minio = client_with_conn
    content = b"verified content"
    real_sha = hashlib.sha256(content).hexdigest()
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, sha256=real_sha)

    obj = MagicMock()
    obj.read = AsyncMock(return_value=content)
    obj.close = MagicMock()
    mock_minio.get_object = AsyncMock(return_value=obj)

    response = await client.get(f"/api/v1/storage/files/{FILE_ID}/verify", headers=TENANT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["integrity_ok"] is True
    assert body["expected_sha256"] == real_sha
    assert body["actual_sha256"] == real_sha


@pytest.mark.asyncio
async def test_verify_integrity_mismatch_is_409(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    # El registro dice "a"*64 pero el objeto en MinIO es otro contenido.
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, sha256="a" * 64)

    obj = MagicMock()
    obj.read = AsyncMock(return_value=b"tampered bytes")
    obj.close = MagicMock()
    mock_minio.get_object = AsyncMock(return_value=obj)

    response = await client.get(f"/api/v1/storage/files/{FILE_ID}/verify", headers=TENANT_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "integrity_check_failed"


@pytest.mark.asyncio
async def test_verify_integrity_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/storage/files/{uuid.uuid4()}/verify", headers=TENANT_HEADERS
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# POST /files/{id}/replace — versionado (RF-DIG-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_creates_new_version(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    current = make_file_record(file_id=FILE_ID)  # v1, sha = "a"*64
    v2 = make_file_record(file_id=uuid.uuid4(), version=2, root_id=FILE_ID, replaces_id=FILE_ID)
    # get_by_id → current; create_version INSERT RETURNING → v2
    mock_conn.fetchrow.side_effect = [current, v2]

    response = await client.post(
        f"/api/v1/storage/files/{FILE_ID}/replace",
        files={"file": ("carta_v2.pdf", b"nuevo contenido distinto", "application/pdf")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    mock_minio.put_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_replace_same_content_no_new_version(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    import hashlib

    client, mock_conn, mock_minio = client_with_conn
    content = b"contenido identico"
    sha = hashlib.sha256(content).hexdigest()
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, sha256=sha)

    response = await client.post(
        f"/api/v1/storage/files/{FILE_ID}/replace",
        files={"file": ("carta.pdf", content, "application/pdf")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200  # sin versión nueva
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_not_current_is_409(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, is_current=False)

    response = await client.post(
        f"/api/v1/storage/files/{FILE_ID}/replace",
        files={"file": ("c.pdf", b"x", "application/pdf")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "not_current_version"


@pytest.mark.asyncio
async def test_replace_not_found_is_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.post(
        f"/api/v1/storage/files/{uuid.uuid4()}/replace",
        files={"file": ("c.pdf", b"x", "application/pdf")},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_versions(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    root = FILE_ID
    v1 = make_file_record(file_id=root, version=1, root_id=root)
    v2 = make_file_record(file_id=uuid.uuid4(), version=2, root_id=root, replaces_id=root)
    mock_conn.fetchrow.return_value = v2  # get_by_id
    mock_conn.fetch.return_value = [v1, v2]  # list_versions

    response = await client.get(f"/api/v1/storage/files/{root}/versions", headers=TENANT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert [v["version"] for v in body] == [1, 2]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["storage"] == "ok"
