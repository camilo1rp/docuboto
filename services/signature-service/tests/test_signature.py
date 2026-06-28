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

import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, settings
from app.core.database import get_tenant_conn
from app.core.security import get_current_user
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
    """POST /sign-xml with valid JWT (PERM_FIRMA) creates a firma record."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = _firma_row(b"<indice/>", USER)

    resp = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<indice/>", "objeto_tipo": "indice", "objeto_id": "exp-1"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "nativa"
    assert body["objeto_tipo"] == "indice"
    assert len(body["payload_sha256"]) == 64
    # firmante_id must come from JWT claims, not from request body
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


# ---------------------------------------------------------------------------
# Unit tests — get_current_user permission & tenant-binding (D-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_user_rejects_role_without_permission() -> None:
    """PERM_FIRMA in roles[] but NOT in permissions[] must yield 403 (D-01).

    Before the fix, the check was against roles[] — a valid RBAC role could
    grant signing rights without the explicit permission, breaking ADR-013.
    """
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core.security import get_current_user

    claims_role_only = {
        "user_id": str(USER),
        "username": "testuser",
        "email": "test@demo.example",
        "tenant_slug": "demo",
        "roles": ["PERM_FIRMA"],  # role present
        "permissions": [],         # permission absent — must be rejected
        "exp": None,
        "iat": None,
    }
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")

    with patch("app.core.security.validate_token", new=AsyncMock(return_value=claims_role_only)):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=creds, x_tenant_slug="demo")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_current_user_rejects_tenant_mismatch() -> None:
    """Token tenant != X-Tenant-Slug must yield 403 — prevents cross-tenant signing (D-01)."""
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core.security import get_current_user

    claims_wrong_tenant = {
        "user_id": str(USER),
        "username": "testuser",
        "email": "test@demo.example",
        "tenant_slug": "other-org",   # token is for a different tenant
        "roles": [],
        "permissions": ["PERM_FIRMA"],
        "exp": None,
        "iat": None,
    }
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")

    with patch("app.core.security.validate_token", new=AsyncMock(return_value=claims_wrong_tenant)):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=creds, x_tenant_slug="demo")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "forbidden"


# ---------------------------------------------------------------------------
# Unit tests — Settings.expected_issuer override (KEYCLOAK_ISSUER)
# ---------------------------------------------------------------------------


def test_settings_expected_issuer_fallback_to_keycloak_url() -> None:
    """Without an override, expected_issuer is derived from keycloak_url."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer=None,
    )
    assert s.expected_issuer == "http://keycloak:8080/realms/orpycamcp"


def test_settings_expected_issuer_override_wins() -> None:
    """When keycloak_issuer is set it takes precedence over keycloak_url."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer="http://localhost:19180/realms/orpycamcp",
    )
    assert s.expected_issuer == "http://localhost:19180/realms/orpycamcp"


def test_issuer_warnings_fire_on_internal_host_without_override() -> None:
    """Caso roto: host interno de Docker y sin KEYCLOAK_ISSUER → aviso fail-fast."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer=None,
    )
    warnings = s.issuer_config_warnings()
    assert len(warnings) == 1
    assert "KEYCLOAK_ISSUER" in warnings[0]


def test_issuer_warnings_silent_with_override() -> None:
    """Dev/prod-split: con override explícito no hay aviso."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer="http://localhost:19180/realms/orpycamcp",
    )
    assert s.issuer_config_warnings() == []


def test_issuer_warnings_silent_on_public_fqdn() -> None:
    """Prod single-host: FQDN público sin override no es un error → sin aviso."""
    s = Settings.model_construct(
        keycloak_url="https://keycloak.example.com",
        keycloak_realm="orpycamcp",
        keycloak_issuer=None,
    )
    assert s.issuer_config_warnings() == []


# ---------------------------------------------------------------------------
# Helpers for JWT integration tests
# ---------------------------------------------------------------------------


def _make_test_rsa_jwks() -> tuple[Any, dict]:
    """Return (private_key, jwks_dict) signed with a fresh RSA-2048 key."""
    import json

    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    priv = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(priv.public_key()))
    jwks: dict = {"keys": [{**jwk_dict, "kid": "test-kid", "use": "sig"}]}
    return priv, jwks


# ---------------------------------------------------------------------------
# Integration tests — validate_token issuer acceptance / rejection
# ---------------------------------------------------------------------------

_PUBLIC_ISSUER = "http://localhost:19180/realms/orpycamcp"
_INTERNAL_ISSUER = "http://keycloak:8080/realms/orpycamcp"


@pytest.mark.asyncio
async def test_validate_token_accepts_public_issuer_via_override() -> None:
    """Token carrying public iss is accepted when keycloak_issuer matches it.

    Reproduces the Docker dev scenario: Keycloak uses KC_HOSTNAME_URL=
    http://localhost:19180 for the public issuer, but backends resolve JWKS
    internally via http://keycloak:8080. Without KEYCLOAK_ISSUER the iss check
    would fail even though the signature is valid.
    """
    import jwt as pyjwt

    from app.core.security import invalidate_jwks_cache, validate_token

    priv, jwks = _make_test_rsa_jwks()

    token = pyjwt.encode(
        {
            "sub": str(USER),
            "iss": _PUBLIC_ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "realm_access": {"roles": []},
            "permissions": ["PERM_FIRMA"],
            "tenant_slug": "demo",
        },
        priv,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )

    override_settings = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer=_PUBLIC_ISSUER,  # override to public URL
        jwks_cache_ttl=300,
    )

    invalidate_jwks_cache()
    with patch("app.core.security._get_jwks", new=AsyncMock(return_value=jwks)):
        with patch("app.core.security.settings", new=override_settings):
            claims = await validate_token(token)

    assert claims["user_id"] == str(USER)
    assert claims["tenant_slug"] == "demo"


@pytest.mark.asyncio
async def test_validate_token_rejects_public_issuer_without_override() -> None:
    """Token with public iss is rejected when keycloak_issuer is not configured.

    expected_issuer defaults to the internal URL (http://keycloak:8080/…) while
    the token carries the public URL — PyJWT raises InvalidIssuerError.
    """
    import jwt as pyjwt

    from app.core.security import invalidate_jwks_cache, validate_token

    priv, jwks = _make_test_rsa_jwks()

    token = pyjwt.encode(
        {
            "sub": str(USER),
            "iss": _PUBLIC_ISSUER,   # public issuer; no override configured below
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "realm_access": {"roles": []},
            "permissions": [],
            "tenant_slug": "demo",
        },
        priv,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )

    # keycloak_issuer=None → expected_issuer resolves to _INTERNAL_ISSUER (mismatch)
    no_override_settings = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer=None,
        jwks_cache_ttl=300,
    )

    invalidate_jwks_cache()
    with patch("app.core.security._get_jwks", new=AsyncMock(return_value=jwks)):
        with patch("app.core.security.settings", new=no_override_settings):
            with pytest.raises(jwt.InvalidIssuerError):
                await validate_token(token)
