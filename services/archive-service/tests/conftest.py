# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for archive-service.

DB dependency (get_db_conn) is mocked so tests run without real infrastructure.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn
from app.main import app

TENANT_SLUG = "demo"
USER_ID = uuid.uuid4()
TENANT_HEADERS = {
    "X-Tenant-Slug": TENANT_SLUG,
    "X-User-Id": str(USER_ID),
}

EXPEDIENTE_ID = uuid.uuid4()
RADICADO_ID = uuid.uuid4()
SERIE_ID = uuid.uuid4()
TRACKING_NUMBER = "2024-DEMO-E-000001"
NOW = datetime.now(tz=UTC)


def make_expediente_record(
    *,
    exp_id: uuid.UUID | None = None,
    code: str = "EXP-2024-0001",
    name: str = "Contrato 2024-001",
    description: str | None = "Contrato de prestación de servicios",
    trd_serie_id: uuid.UUID | None = None,
    exp_status: str = "open",
    opened_by: uuid.UUID | None = None,
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
    updated_at: datetime | None = None,
    metadata: dict | None = None,
    metadata_template_id: uuid.UUID | None = None,
    disposition: dict | None = None,
) -> dict:
    return {
        "id": exp_id or EXPEDIENTE_ID,
        "code": code,
        "name": name,
        "description": description,
        "trd_serie_id": trd_serie_id,
        "status": exp_status,
        "opened_by": opened_by or USER_ID,
        "opened_at": opened_at or NOW,
        "closed_at": closed_at,
        "updated_at": updated_at or NOW,
        "metadata": metadata if metadata is not None else {},
        "metadata_template_id": metadata_template_id,
        "disposition": disposition,
    }


def make_radicado_link_record(
    *,
    expediente_id: uuid.UUID | None = None,
    radicado_id: uuid.UUID | None = None,
    tracking_number: str = TRACKING_NUMBER,
    added_at: datetime | None = None,
    content_hash: str | None = None,
) -> dict:
    return {
        "expediente_id": expediente_id or EXPEDIENTE_ID,
        "radicado_id": radicado_id or RADICADO_ID,
        "tracking_number": tracking_number,
        "added_at": added_at or NOW,
        "content_hash": content_hash,
    }


def make_trd_serie_record(
    *,
    serie_id: uuid.UUID | None = None,
    code: str = "100",
    name: str = "Contratos",
    description: str | None = "Series contractuales",
    retention_years: int = 5,
    total_retention: int = 10,
    disposition: str = "conserve",
    is_active: bool = True,
    created_at: datetime | None = None,
    parent_id: uuid.UUID | None = None,
    archivo_gestion_years: int | None = 5,
    archivo_central_years: int | None = 5,
    version: int = 1,
    valid_from=None,
) -> dict:
    return {
        "id": serie_id or SERIE_ID,
        "code": code,
        "name": name,
        "description": description,
        "retention_years": retention_years,
        "total_retention": total_retention,
        "disposition": disposition,
        "is_active": is_active,
        "created_at": created_at or NOW,
        "parent_id": parent_id,
        "archivo_gestion_years": archivo_gestion_years,
        "archivo_central_years": archivo_central_years,
        "version": version,
        "valid_from": valid_from or NOW.date(),
    }


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 0
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # `async with conn.transaction():` → context manager (índice E15, etc.).
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


@pytest_asyncio.fixture(scope="function")
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) so tests can configure mock return values per-call."""
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_conn

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Simple client — use when you don't need to inspect mock internals."""
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
