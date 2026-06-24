# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for storage-service.

DB dependency (get_db_conn) and MinIO client (get_minio) are both mocked
so tests run without real infrastructure.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn
from app.main import app

TENANT_SLUG = "demo"
TENANT_HEADERS = {
    "X-Tenant-Slug": TENANT_SLUG,
    "X-User-Id": str(uuid.uuid4()),
}

FILE_ID = uuid.uuid4()
SHA256 = "a" * 64
NOW = datetime.now(tz=UTC)


def make_file_record(
    *,
    file_id: uuid.UUID | None = None,
    filename: str = "carta.pdf",
    mime_type: str | None = "application/pdf",
    file_size: int = 12345,
    sha256: str = SHA256,
    bucket: str = f"orpycamcp-{TENANT_SLUG}-documents",
    object_key: str | None = None,
    uploaded_by: uuid.UUID | None = None,
    uploaded_at: datetime | None = None,
    version: int = 1,
    is_current: bool = True,
    root_id: uuid.UUID | None = None,
    replaces_id: uuid.UUID | None = None,
) -> dict:
    fid = file_id or FILE_ID
    return {
        "id": fid,
        "filename": filename,
        "mime_type": mime_type,
        "file_size": file_size,
        "sha256": sha256,
        "bucket": bucket,
        "object_key": object_key or f"{fid}/carta.pdf",
        "uploaded_by": uploaded_by or uuid.uuid4(),
        "uploaded_at": uploaded_at or NOW,
        "version": version,
        "is_current": is_current,
        "root_id": root_id or fid,
        "replaces_id": replaces_id,
    }


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.fetch.return_value = []
    # `async with conn.transaction()` (create_version atómico).
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _make_mock_minio() -> MagicMock:
    minio = MagicMock()
    minio.bucket_exists = AsyncMock(return_value=True)
    minio.make_bucket = AsyncMock()
    minio.put_object = AsyncMock()
    minio.presigned_get_object = AsyncMock(return_value="http://minio/presigned-url")
    minio.remove_object = AsyncMock()
    minio.list_buckets = AsyncMock(return_value=[])
    return minio


def _minio_patches(mock_minio: MagicMock):
    """
    Patch get_minio in all modules that import it at module level.

    health router imports `from app.core.minio_client import get_minio` so its
    local name must be patched independently from the source module.
    """
    return (
        patch("app.core.minio_client.get_minio", return_value=mock_minio),
        patch("app.services.storage_service.get_minio", return_value=mock_minio),
        patch("app.routers.health.get_minio", return_value=mock_minio),
    )


@pytest_asyncio.fixture(scope="function")
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock, MagicMock], None]:
    """Yields (client, mock_conn, mock_minio) so tests can configure mocks per-call."""
    mock_conn = _make_mock_conn()
    mock_minio = _make_mock_minio()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    p1, p2, p3 = _minio_patches(mock_minio)
    with p1, p2, p3:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, mock_conn, mock_minio

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Simple client fixture — use when you don't need to configure mock internals."""
    mock_conn = _make_mock_conn()
    mock_minio = _make_mock_minio()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    p1, p2, p3 = _minio_patches(mock_minio)
    with p1, p2, p3:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()
