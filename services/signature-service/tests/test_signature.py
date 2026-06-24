# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del signature-service (E06, ADR-016)."""

import uuid
from datetime import UTC, datetime

import pytest

from app.services import signer
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
USER = uuid.uuid4()


def test_signer_native_deterministic_and_detects_tamper() -> None:
    blob = signer.firmar(b"<indice/>", str(USER))["firma_blob"]
    assert signer.verificar(b"<indice/>", str(USER), blob) is True
    assert signer.verificar(b"<indice ALTERADO/>", str(USER), blob) is False
    assert signer.verificar(b"<indice/>", str(uuid.uuid4()), blob) is False


def _firma_row(payload: bytes, firmante: uuid.UUID) -> dict:
    r = signer.firmar(payload, str(firmante))
    return {
        "id": uuid.uuid4(),
        "objeto_tipo": "indice",
        "objeto_id": "exp-1",
        "formato_solicitado": "XAdES",
        "firmante_id": firmante,
        "payload_sha256": r["payload_sha256"],
        "firma_blob": r["firma_blob"],
        "provider": r["provider"],
        "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_sign_xml(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = _firma_row(b"<indice/>", USER)
    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={
            "payload_xml": "<indice/>",
            "objeto_tipo": "indice",
            "objeto_id": "exp-1",
            "formato": "XAdES",
        },
        headers={**TENANT_HEADERS, "X-User-Id": str(USER)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "nativa"
    assert body["objeto_tipo"] == "indice"
    assert len(body["payload_sha256"]) == 64


@pytest.mark.asyncio
async def test_verify_ok_and_tamper(client_with_conn) -> None:
    client, conn = client_with_conn
    row = _firma_row(b"<indice/>", USER)
    conn.fetchrow.return_value = row

    ok = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice/>"},
        headers=TENANT_HEADERS,
    )
    assert ok.status_code == 200 and ok.json()["valida"] is True

    bad = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice ALTERADO/>"},
        headers=TENANT_HEADERS,
    )
    assert bad.json()["valida"] is False


@pytest.mark.asyncio
async def test_verify_not_found(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = None
    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(uuid.uuid4()), "payload_xml": "<x/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
