# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de firma electrónica (E06, ADR-016)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

_HASH = "a" * 64
_USER = str(uuid.uuid4())


def _sig_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "anexo_id": None,
        "signer_id": uuid.UUID(_USER),
        "signer_name": "Ana Firmante",
        "content_hash": _HASH,
        "reason": "Aprobación",
        "signed_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_sign_document(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _sig_row()
    headers = {**TENANT_HEADERS, "X-User-Id": _USER, "X-Username": "Ana Firmante"}

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"content_hash": _HASH, "reason": "Aprobación"},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["signer_name"] == "Ana Firmante"
    assert body["content_hash"] == _HASH


@pytest.mark.asyncio
async def test_sign_rejects_bad_hash(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/signatures",
        json={"content_hash": "not-a-sha256"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_verify_valid(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_sig_row()]

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/signatures/verify?content_hash={_HASH}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert len(body["signatures"]) == 1


@pytest.mark.asyncio
async def test_verify_invalid_when_no_match(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []  # contenido alterado → ninguna firma coincide

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/signatures/verify?content_hash={'b' * 64}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
