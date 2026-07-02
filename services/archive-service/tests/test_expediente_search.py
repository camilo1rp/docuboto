# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de búsqueda full-text de expedientes (E09, ADR-014)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS


def _hit_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "code": "EXP-2024-0001",
        "name": "Contratación servicios jurídicos",
        "status": "open",
        "opened_at": datetime.now(tz=UTC),
        "rank": 0.4,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_search_expedientes_with_text(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_hit_row()]

    resp = await client.get(
        "/api/v1/expedientes/search?q=juridic&status=open", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "1"
    body = resp.json()
    assert body["total"] == 1
    assert body["hits"][0]["code"] == "EXP-2024-0001"
    where_sql = mock_conn.fetchval.call_args[0][0]
    assert "websearch_to_tsquery" in where_sql


@pytest.mark.asyncio
async def test_search_expedientes_filters_only(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # [0]=D-08 gate (USUA_PERM_EXPEDIENTE granted), [1]=COUNT(*) de la búsqueda → 0
    mock_conn.fetchval.side_effect = [True, 0]
    mock_conn.fetch.return_value = []

    resp = await client.get("/api/v1/expedientes/search?meta.area=juridica", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    rows_sql = mock_conn.fetch.call_args[0][0]
    assert "websearch_to_tsquery" not in rows_sql
    assert "metadata @>" in mock_conn.fetchval.call_args[0][0]


@pytest.mark.asyncio
async def test_search_expedientes_always_filters_by_clearance(client_with_conn) -> None:
    """RF-SEG-08 / RF-BUS-04: el WHERE siempre acota por nivel de seguridad; con el
    usuario sin clearance (fetchrow→None) el límite es PUBLICA (1)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # sin clearance asignado → PUBLICA
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_hit_row()]

    resp = await client.get("/api/v1/expedientes/search?q=juridic", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    count_call = mock_conn.fetchval.call_args[0]
    assert "nivel_seguridad <=" in count_call[0]
    assert count_call[1] == 1  # clearance = PUBLICA (primer parámetro vinculado)
    assert "nivel_seguridad <=" in mock_conn.fetch.call_args[0][0]


@pytest.mark.asyncio
async def test_search_expedientes_requires_tenant(client_with_conn) -> None:
    """Sin X-Tenant-Slug, el gate RBAC (`require_permission`) responde 403
    (fail-closed) antes de que `_require_tenant` del handler emita su 400 —
    ver `test_create_expediente_missing_tenant` en test_archive.py."""
    client, _ = client_with_conn
    resp = await client.get("/api/v1/expedientes/search?q=algo")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
