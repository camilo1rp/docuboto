# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del signature-service (E06, ADR-016, RF-FIR-11..14, D-01)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_tenant_conn
from app.main import app
from app.services import signer
from tests.conftest import TENANT_HEADERS, USER

# Fixed timestamp used to build test firma rows so verify tests are deterministic.
SELLO_DT = datetime(2024, 1, 1, tzinfo=UTC)
SELLO_STR = SELLO_DT.astimezone(UTC).isoformat()
NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Unit tests — signer module
# ---------------------------------------------------------------------------


def test_signer_hmac_verify_roundtrip() -> None:
    """Sign and verify succeed for same inputs."""
    secret = settings.signature_secret
    result = signer.firmar(b"<indice/>", str(USER), SELLO_STR, secret)
    assert signer.verificar(b"<indice/>", str(USER), result["firma_blob"], SELLO_STR, secret) is True


def test_signer_detects_tampered_payload() -> None:
    secret = settings.signature_secret
    blob = signer.firmar(b"<indice/>", str(USER), SELLO_STR, secret)["firma_blob"]
    assert signer.verificar(b"<indice ALTERADO/>", str(USER), blob, SELLO_STR, secret) is False


def test_signer_detects_wrong_firmante() -> None:
    secret = settings.signature_secret
    blob = signer.firmar(b"<indice/>", str(USER), SELLO_STR, secret)["firma_blob"]
    assert signer.verificar(b"<indice/>", str(uuid.uuid4()), blob, SELLO_STR, secret) is False


def test_signer_non_reproducible_without_secret() -> None:
    """firma_blob is not reproducible by a caller who doesn't know the secret."""
    secret = settings.signature_secret
    blob = signer.firmar(b"<indice/>", str(USER), SELLO_STR, secret)["firma_blob"]
    assert signer.verificar(b"<indice/>", str(USER), blob, SELLO_STR, "wrong-secret") is False


def test_signer_detects_wrong_sello_tiempo() -> None:
    secret = settings.signature_secret
    blob = signer.firmar(b"<indice/>", str(USER), SELLO_STR, secret)["firma_blob"]
    assert signer.verificar(b"<indice/>", str(USER), blob, "2025-06-01T00:00:00+00:00", secret) is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _firma_row(payload: bytes, firmante: uuid.UUID) -> dict:
    """Build a fake DB row matching FirmaRepository._COLS."""
    r = signer.firmar(payload, str(firmante), SELLO_STR, settings.signature_secret)
    return {
        "id": uuid.uuid4(),
        "objeto_tipo": "indice",
        "objeto_id": "exp-1",
        "formato_solicitado": "XAdES",
        "firmante_id": firmante,
        "payload_sha256": r["payload_sha256"],
        "firma_blob": r["firma_blob"],
        "provider": r["provider"],
        "sello_tiempo": SELLO_DT,
        "created_at": NOW,
    }


# ---------------------------------------------------------------------------
# HTTP tests — sign-xml
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_happy_path(client_with_conn) -> None:
    """POST /sign-xml with valid X-User-Id (PERM_FIRMA granted) creates a firma record.

    Uses objeto_tipo="radicado" (not "indice") on purpose: since Inc.1 (E06,
    ADR-016) objeto_tipo="indice" with the default settings.signer_provider=
    "xades_local" routes to the REAL XAdES-B provider (app.providers.local),
    which needs a mounted seal — see tests/test_sign_xml_xades.py for that
    coverage. This test keeps exercising the preexisting HMAC 'nativa' path
    end-to-end through the router, which any objeto_tipo other than "indice"
    still selects (app.services.signing_service._select_provider_name).
    """
    client, conn = client_with_conn
    conn.fetchrow.return_value = _firma_row(b"<indice/>", USER)

    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<indice/>", "objeto_tipo": "radicado", "objeto_id": "exp-1"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "nativa"
    assert body["objeto_tipo"] == "indice"
    assert len(body["payload_sha256"]) == 64
    # firmante_id must come from X-User-Id header, not from request body
    assert body["firmante_id"] == str(USER)


@pytest.mark.asyncio
async def test_sign_xml_body_firmante_id_ignored() -> None:
    """firmante_id in the request body must not exist in the schema (RF-FIR-11)."""
    from app.schemas.firma import SignXmlRequest

    # Pydantic should reject or ignore a firmante_id field — it's not in the schema.
    req = SignXmlRequest(payload_xml="<x/>", objeto_tipo="indice", firmante_id=str(uuid.uuid4()))  # type: ignore[call-arg]
    # Pydantic v2 with extra="ignore" silently drops unknown fields.
    assert not hasattr(req, "firmante_id")


@pytest.mark.asyncio
async def test_sign_xml_requires_auth() -> None:
    """POST /sign-xml without X-User-Id header returns 401 (require_permission gate, RF-FIR-11)."""
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.transaction = MagicMock(return_value=AsyncMock())

    async def _conn_dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": "indice"},
                headers={"X-Tenant-Slug": "demo"},  # X-User-Id intentionally omitted
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_sign_xml_requires_perm_firma() -> None:
    """Valid X-User-Id but PERM_FIRMA denied in DB → 403 forbidden (RF-FIR-11 / ADR-013).

    check_permission returns False (fetchval=None → user not found / no perm).
    require_permission raises 403. The router must propagate it.
    """
    conn = AsyncMock()
    conn.fetchval.return_value = None  # check_permission → denied
    conn.transaction = MagicMock(return_value=AsyncMock())

    async def _conn_dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<x/>", "objeto_tipo": "indice"},
                headers=TENANT_HEADERS,  # includes X-User-Id
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "forbidden"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# HTTP tests — verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_ok(client_with_conn) -> None:
    """POST /verify returns valida=true for untampered payload."""
    client, conn = client_with_conn
    row = _firma_row(b"<indice/>", USER)
    conn.fetchrow.return_value = row

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valida"] is True


@pytest.mark.asyncio
async def test_verify_tampered_payload(client_with_conn) -> None:
    """POST /verify returns valida=false when payload differs from signed content."""
    client, conn = client_with_conn
    row = _firma_row(b"<indice/>", USER)
    conn.fetchrow.return_value = row

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(row["id"]), "payload_xml": "<indice ALTERADO/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valida"] is False


@pytest.mark.asyncio
async def test_verify_not_found(client_with_conn) -> None:
    """POST /verify returns 404 when firma_id does not exist."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(uuid.uuid4()), "payload_xml": "<x/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
