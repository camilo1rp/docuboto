# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests — TOTP (RFC 6238) para la firma personal (E06).

Same mocked-asyncpg-connection style as tests/test_clearance.py: no real
Postgres, `get_caller`/`get_tenant_conn` overridden directly.

``app.services.totp_service._audit.append`` is monkeypatched to a no-op
AsyncMock in every test (mirrors signature-service's tests/test_cadena_2fa.py
``_patch_audit`` technique) — it decouples these tests from
orpycamcp_common.audit's own fetchval/execute call sequence, which is a
different module's implementation detail.

Cryptography is exercised FOR REAL (no mocking of AESGCM or the RFC 6238
math) — `settings.totp_encryption_key` is set to a fixed base64 32-byte test
key. Valid TOTP codes are generated with the real current wall-clock time
via `app.core.totp.current_timestep`/`_hotp` — no sleep() needed anywhere.
"""

from __future__ import annotations

import asyncio
import base64
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.exceptions import InvalidTag
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.config import settings
from app.core.database import get_db_conn, get_tenant_conn
from app.core.totp import (
    _hotp,
    build_aad,
    build_otpauth_uri,
    classify_totp_attempt,
    current_timestep,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
)
from app.main import app
from app.services.totp_service import TotpService

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TOTP_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
TEST_KEY = base64.b64encode(b"0" * 32).decode()


_STATUS_TOKEN = "test-internal-token"


@pytest.fixture(autouse=True)
def _internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "signing_key_internal_token", _STATUS_TOKEN)


class _FakeTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _totp_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "totp_encryption_key", TEST_KEY)


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_append = AsyncMock(return_value="fakehash")
    monkeypatch.setattr("app.services.totp_service._audit.append", mock_append)
    return mock_append


def _caller() -> Caller:
    return Caller(user_id=USER_ID, keycloak_sub="kc-sub", is_root=False, permissions={})


def _make_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    conn.execute.return_value = "UPDATE 1"
    return conn


def _override_public(conn: AsyncMock, caller: Caller | None = None) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    app.dependency_overrides[get_caller] = lambda: (caller or _caller())


def _override_internal(conn: AsyncMock) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_db_conn] = _conn_dep


def _clear() -> None:
    app.dependency_overrides.clear()


def _aad(tenant_slug: str = "demo", user_id: uuid.UUID = USER_ID) -> bytes:
    return build_aad(tenant_slug=tenant_slug, user_id=str(user_id))


def _encrypt(secret: bytes, *, tenant_slug: str = "demo", user_id: uuid.UUID = USER_ID):
    return encrypt_secret(secret, key=base64.b64decode(TEST_KEY), aad=_aad(tenant_slug, user_id))


def _valid_code(secret: bytes, *, digits: int = 6) -> tuple[str, int]:
    """Real, current-time-valid TOTP code + its timestep."""
    t = current_timestep()
    return _hotp(secret, t, digits), t


def _totp_row(
    *,
    secret: bytes,
    estado: str = "pendiente_activacion",
    last_timestep: int = 0,
    failed_attempts: int = 0,
    locked_until: datetime | None = None,
    activated_at: datetime | None = None,
    tenant_slug: str = "demo",
    user_id: uuid.UUID = USER_ID,
) -> dict:
    ciphertext, nonce = _encrypt(secret, tenant_slug=tenant_slug, user_id=user_id)
    return {
        "id": TOTP_ID,
        "user_id": user_id,
        "secret_cifrado": ciphertext,
        "secret_nonce": nonce,
        "algoritmo": "SHA1",
        "digitos": 6,
        "periodo": 30,
        "estado": estado,
        "last_timestep": last_timestep,
        "failed_attempts": failed_attempts,
        "locked_until": locked_until,
        "key_version": 1,
        "created_at": datetime.now(UTC),
        "activated_at": activated_at,
        "updated_at": datetime.now(UTC),
    }


def _user_row(user_id: uuid.UUID = USER_ID) -> dict:
    return {
        "id": user_id,
        "username": "OPERADOR",
        "email": "operador@demo.orpycamcp.local",
        "keycloak_sub": "kc-sub",
        "is_root": False,
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# RFC 6238 §Appendix B test vectors (SHA1, 8 digits, secret "12345678901234567890")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unix_time,expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
    ],
)
def test_rfc6238_appendix_b_vectors(unix_time: int, expected: str) -> None:
    secret = b"12345678901234567890"  # ASCII, 20 bytes — RFC 6238 Appendix B fixture
    t = current_timestep(period=30, now=float(unix_time))
    assert _hotp(secret, t, digits=8) == expected


# ---------------------------------------------------------------------------
# classify_totp_attempt — valid / replay / invalid / skew
# ---------------------------------------------------------------------------


def test_classify_valid_code() -> None:
    secret = generate_secret()
    code, t = _valid_code(secret)
    attempt = classify_totp_attempt(secret, code, last_timestep=0, skew=1)
    assert attempt.outcome == "valid"
    assert attempt.timestep == t


def test_classify_replay_when_timestep_not_advancing() -> None:
    secret = generate_secret()
    code, t = _valid_code(secret)
    # last_timestep already AT the matched timestep → not > last_timestep → replay.
    attempt = classify_totp_attempt(secret, code, last_timestep=t, skew=1)
    assert attempt.outcome == "replay"


def test_classify_invalid_code() -> None:
    secret = generate_secret()
    attempt = classify_totp_attempt(secret, "000000", last_timestep=0, skew=1)
    # Astronomically unlikely for "000000" to collide with any of the 3
    # candidate HMACs — deterministic in practice.
    assert attempt.outcome == "invalid"


def test_classify_accepts_skew_minus_one() -> None:
    secret = generate_secret()
    t = current_timestep()
    code_prev = _hotp(secret, t - 1)
    attempt = classify_totp_attempt(secret, code_prev, last_timestep=0, skew=1)
    assert attempt.outcome == "valid"
    assert attempt.timestep == t - 1


def test_classify_accepts_skew_plus_one() -> None:
    """The other half of the [-1, +1] window — a clock running slightly
    ahead of the server must also be accepted (RFC 6238 §5.2)."""
    secret = generate_secret()
    t = current_timestep()
    code_next = _hotp(secret, t + 1)
    attempt = classify_totp_attempt(secret, code_next, last_timestep=0, skew=1)
    assert attempt.outcome == "valid"
    assert attempt.timestep == t + 1


def test_classify_rejects_outside_skew_window() -> None:
    secret = generate_secret()
    t = current_timestep()
    code_far = _hotp(secret, t - 5)
    attempt = classify_totp_attempt(secret, code_far, last_timestep=0, skew=1)
    assert attempt.outcome == "invalid"


# ---------------------------------------------------------------------------
# AESGCM encryption at rest — AAD binds ciphertext to row/tenant
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip() -> None:
    secret = generate_secret()
    key = base64.b64decode(TEST_KEY)
    aad = build_aad(tenant_slug="demo", user_id=str(USER_ID))
    ciphertext, nonce = encrypt_secret(secret, key=key, aad=aad)
    assert decrypt_secret(ciphertext, nonce=nonce, key=key, aad=aad) == secret


def test_decrypt_fails_with_aad_from_another_row() -> None:
    """A ciphertext copied to another user's/tenant's row fails to decrypt."""
    secret = generate_secret()
    key = base64.b64decode(TEST_KEY)
    aad_own = build_aad(tenant_slug="demo", user_id=str(USER_ID))
    ciphertext, nonce = encrypt_secret(secret, key=key, aad=aad_own)

    aad_other = build_aad(tenant_slug="demo", user_id=str(uuid.uuid4()))
    with pytest.raises(InvalidTag):
        decrypt_secret(ciphertext, nonce=nonce, key=key, aad=aad_other)

    aad_other_tenant = build_aad(tenant_slug="otro-tenant", user_id=str(USER_ID))
    with pytest.raises(InvalidTag):
        decrypt_secret(ciphertext, nonce=nonce, key=key, aad=aad_other_tenant)


def test_otpauth_uri_never_logged_contains_secret_once() -> None:
    secret = generate_secret()
    uri = build_otpauth_uri(secret=secret, issuer="OrpycaMCP", account_name="op@demo.test")
    assert uri.startswith("otpauth://totp/")
    assert "secret=" in uri


# ---------------------------------------------------------------------------
# POST /api/v1/auth/me/totp/enroll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_fresh_returns_otpauth_uri(client: AsyncClient) -> None:
    conn = _make_conn()
    new_secret = generate_secret()
    ciphertext, nonce = _encrypt(new_secret)
    new_row = _totp_row(secret=new_secret)
    conn.fetchrow.side_effect = [None, new_row, _user_row()]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["estado"] == "pendiente_activacion"
        assert body["otpauth_uri"].startswith("otpauth://totp/")
        assert "operador%40demo.orpycamcp.local" in body["otpauth_uri"]
        assert resp.headers["cache-control"] == "no-store"
        # Never leaks ciphertext/nonce/secret in any other field.
        assert set(body.keys()) == {"otpauth_uri", "estado"}
    finally:
        _clear()


@pytest.mark.asyncio
async def test_enroll_replaces_pending_without_step_up(client: AsyncClient) -> None:
    conn = _make_conn()
    old_secret = generate_secret()
    pending_row = _totp_row(secret=old_secret, estado="pendiente_activacion")
    new_secret = generate_secret()
    new_row = _totp_row(secret=new_secret)
    conn.fetchrow.side_effect = [pending_row, new_row, _user_row()]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 201
        # Old row revoked.
        revoke_calls = [
            c for c in conn.execute.call_args_list if "estado = 'revocado'" in str(c.args[0])
        ]
        assert len(revoke_calls) == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_enroll_active_without_step_up_returns_409(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    active_row = _totp_row(secret=secret, estado="activo")
    conn.fetchrow.side_effect = [active_row]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "totp_ya_activo"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_enroll_active_with_wrong_step_up_returns_401(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    active_row = _totp_row(secret=secret, estado="activo")
    conn.fetchrow.side_effect = [active_row]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll",
            json={"step_up_totp_code": "000000"},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "step_up_invalido"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_enroll_active_with_correct_step_up_reenrolls(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    active_row = _totp_row(secret=secret, estado="activo", last_timestep=0)
    code, _ = _valid_code(secret)
    new_secret = generate_secret()
    new_row = _totp_row(secret=new_secret)
    conn.fetchrow.side_effect = [active_row, new_row, _user_row()]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll",
            json={"step_up_totp_code": code},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 201
        revoke_calls = [
            c for c in conn.execute.call_args_list if "estado = 'revocado'" in str(c.args[0])
        ]
        assert len(revoke_calls) == 1
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /api/v1/auth/me/totp/activate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_success(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    pending_row = _totp_row(secret=secret, estado="pendiente_activacion", last_timestep=0)
    code, _ = _valid_code(secret)
    activated_row = _totp_row(
        secret=secret, estado="activo", activated_at=datetime.now(UTC)
    )
    conn.fetchrow.side_effect = [pending_row, activated_row]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/activate", json={"code": code}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "activo"
        activate_calls = [
            c for c in conn.execute.call_args_list if "estado = 'activo'" in str(c.args[0])
        ]
        assert len(activate_calls) == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_activate_wrong_code_returns_401_and_persists_failure(
    client: AsyncClient,
) -> None:
    conn = _make_conn()
    secret = generate_secret()
    pending_row = _totp_row(secret=secret, estado="pendiente_activacion", failed_attempts=0)
    failure_row = {"failed_attempts": 1, "locked_until": None}
    conn.fetchrow.side_effect = [pending_row, failure_row]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/activate", json={"code": "000000"}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "codigo_invalido"
        failure_calls = [
            c
            for c in conn.fetchrow.call_args_list
            if "failed_attempts = failed_attempts + 1" in str(c.args[0])
        ]
        assert len(failure_calls) == 1
        # Atomic increment + lockout decision happen server-side (RETURNING),
        # never a Python-computed absolute value passed in.
        assert failure_calls[0].args[2] == settings.totp_max_failed_attempts
        assert failure_calls[0].args[3] == settings.totp_lockout_seconds
    finally:
        _clear()


@pytest.mark.asyncio
async def test_activate_no_pending_returns_409(client: AsyncClient) -> None:
    conn = _make_conn()
    conn.fetchrow.side_effect = [None]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/activate", json={"code": "123456"}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "totp_no_pendiente"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_activate_locked_returns_423_before_comparing_code(
    client: AsyncClient,
) -> None:
    conn = _make_conn()
    secret = generate_secret()
    locked_row = _totp_row(
        secret=secret,
        estado="pendiente_activacion",
        locked_until=datetime.now(UTC) + timedelta(minutes=10),
    )
    conn.fetchrow.side_effect = [locked_row]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/activate", json={"code": "000000"}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 423
        assert resp.json()["detail"]["error"] == "totp_bloqueado"
        # Locked out BEFORE any comparison — no failure counter mutation here.
        failure_calls = [
            c
            for c in conn.fetchrow.call_args_list
            if "failed_attempts = failed_attempts + 1" in str(c.args[0])
        ]
        assert len(failure_calls) == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_activate_lockout_after_max_failed_attempts(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    # One failure away from the default threshold (5). The RETURNING row
    # reflects what the atomic UPDATE would compute server-side.
    pending_row = _totp_row(secret=secret, estado="pendiente_activacion", failed_attempts=4)
    failure_row = {
        "failed_attempts": settings.totp_max_failed_attempts,
        "locked_until": datetime.now(UTC) + timedelta(seconds=settings.totp_lockout_seconds),
    }
    conn.fetchrow.side_effect = [pending_row, failure_row]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/activate", json={"code": "000000"}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 401
        failure_calls = [
            c
            for c in conn.fetchrow.call_args_list
            if "failed_attempts = failed_attempts + 1" in str(c.args[0])
        ]
        assert failure_calls[0].args[2] == settings.totp_max_failed_attempts
        assert failure_calls[0].args[3] == settings.totp_lockout_seconds
    finally:
        _clear()


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me/totp — never reveals the secret
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_no_configurado(client: AsyncClient) -> None:
    conn = _make_conn()
    conn.fetchrow.side_effect = [None]
    _override_public(conn)
    try:
        resp = await client.get("/api/v1/auth/me/totp", headers=TENANT_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {
            "estado": "no_configurado",
            "activated_at": None,
            "locked_until": None,
            # Sin credencial no hay códigos que contar. El 0 se declara igual,
            # nunca se omite: un consumidor no debe poder leer su ausencia
            # como "no aplica" (mismo criterio fail-closed que `permissions`
            # en /auth/me).
            "recovery_codes_restantes": 0,
        }
    finally:
        _clear()


@pytest.mark.asyncio
async def test_status_never_includes_secret(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    active_row = _totp_row(secret=secret, estado="activo", activated_at=datetime.now(UTC))
    conn.fetchrow.side_effect = [active_row]
    _override_public(conn)
    try:
        resp = await client.get("/api/v1/auth/me/totp", headers=TENANT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["estado"] == "activo"
        assert set(body.keys()) == {
            "estado",
            "activated_at",
            "locked_until",
            "recovery_codes_restantes",
        }
        # El conteo es un número, jamás los códigos.
        assert isinstance(body["recovery_codes_restantes"], int)
        assert "secret_cifrado" not in body and "secret_nonce" not in body
    finally:
        _clear()


# ---------------------------------------------------------------------------
# DELETE /api/v1/auth/me/totp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_no_totp_returns_404(client: AsyncClient) -> None:
    conn = _make_conn()
    conn.fetchrow.side_effect = [None]
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE", "/api/v1/auth/me/totp", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "no_totp"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_revoke_pending_needs_no_step_up(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    pending_row = _totp_row(secret=secret, estado="pendiente_activacion")
    conn.fetchrow.side_effect = [pending_row]
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE", "/api/v1/auth/me/totp", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "revocado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_revoke_active_without_step_up_returns_401(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    active_row = _totp_row(secret=secret, estado="activo")
    conn.fetchrow.side_effect = [active_row]
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE", "/api/v1/auth/me/totp", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "step_up_invalido"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_revoke_active_with_correct_step_up(client: AsyncClient) -> None:
    conn = _make_conn()
    secret = generate_secret()
    active_row = _totp_row(secret=secret, estado="activo", last_timestep=0)
    code, _ = _valid_code(secret)
    conn.fetchrow.side_effect = [active_row]
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/totp",
            json={"step_up_totp_code": code},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "revocado"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /internal/totp/verify — service-to-service (signature-service)
# ---------------------------------------------------------------------------


def _mock_validate_token(monkeypatch: pytest.MonkeyPatch, claims: dict) -> None:
    monkeypatch.setattr(
        "app.routers.totp.validate_token", AsyncMock(return_value=claims)
    )


@pytest.mark.asyncio
async def test_internal_verify_success_advances_timestep(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    secret = generate_secret()
    code, t = _valid_code(secret)
    active_row = _totp_row(secret=secret, estado="activo", last_timestep=0)
    conn.fetchrow.side_effect = [_user_row(), active_row]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": code},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valido"] is True
        assert body["timestep"] == t
        assert body["factor_tipo"] == "totp"
        # AREA-04-05: dice DE QUIÉN era el factor, para que el llamante lo cruce
        # con el actor al que atribuye el acto.
        assert body["user_id"] == str(USER_ID)
        advance_calls = [
            c for c in conn.execute.call_args_list if "last_timestep = $2" in str(c.args[0])
        ]
        assert len(advance_calls) == 1
        assert advance_calls[0].args[1:] == (USER_ID, t)
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_replay_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code whose matched timestep is not > last_timestep is a replay,
    never a success — even though the HMAC itself is correct."""
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    secret = generate_secret()
    t = current_timestep()
    code = _hotp(secret, t)
    # last_timestep already at t → this code was already consumed.
    active_row = _totp_row(secret=secret, estado="activo", last_timestep=t)
    failure_row = {"failed_attempts": 1, "locked_until": None}
    conn.fetchrow.side_effect = [_user_row(), active_row, failure_row]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": code},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valido"] is False
        assert body["motivo"] == "replay"
        advance_calls = [
            c for c in conn.execute.call_args_list if "last_timestep = $2" in str(c.args[0])
        ]
        assert len(advance_calls) == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_wrong_code_returns_codigo_incorrecto(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code that matches no timestep in the skew window (not a replay of a
    previously-valid one) is reported as ``codigo_incorrecto`` — with the
    remaining-attempts counter — and persists the failure, never a 500."""
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    secret = generate_secret()
    active_row = _totp_row(secret=secret, estado="activo", last_timestep=0, failed_attempts=0)
    failure_row = {"failed_attempts": 1, "locked_until": None}
    conn.fetchrow.side_effect = [_user_row(), active_row, failure_row]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "000000"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valido"] is False
        assert body["motivo"] == "codigo_incorrecto"
        assert body["intentos_restantes"] == settings.totp_max_failed_attempts - 1
        failure_calls = [
            c
            for c in conn.fetchrow.call_args_list
            if "failed_attempts = failed_attempts + 1" in str(c.args[0])
        ]
        assert len(failure_calls) == 1
        assert failure_calls[0].args[2] == settings.totp_max_failed_attempts
        assert failure_calls[0].args[3] == settings.totp_lockout_seconds
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_lockout_after_max_failed_attempts(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors ``test_activate_lockout_after_max_failed_attempts`` for the
    service-to-service verify path: reaching the threshold sets
    ``locked_until`` atomically with the failure that trips it."""
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    secret = generate_secret()
    # One failure away from the default threshold (5). The RETURNING row
    # reflects what the atomic UPDATE would compute server-side.
    active_row = _totp_row(secret=secret, estado="activo", last_timestep=0, failed_attempts=4)
    failure_row = {
        "failed_attempts": settings.totp_max_failed_attempts,
        "locked_until": datetime.now(UTC) + timedelta(seconds=settings.totp_lockout_seconds),
    }
    conn.fetchrow.side_effect = [_user_row(), active_row, failure_row]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "000000"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valido"] is False
        assert body["motivo"] == "codigo_incorrecto"
        assert body["intentos_restantes"] == 0
        assert body["locked_until"] is not None
        failure_calls = [
            c
            for c in conn.fetchrow.call_args_list
            if "failed_attempts = failed_attempts + 1" in str(c.args[0])
        ]
        assert failure_calls[0].args[2] == settings.totp_max_failed_attempts
        assert failure_calls[0].args[3] == settings.totp_lockout_seconds
    finally:
        _clear()


class _ConcurrentFailureConn:
    """Minimal fake ``auth_user_totp`` connection for a single regression
    test: N concurrent failed ``TotpService.verify()`` calls must never lose
    an increment (the D1 lost-update bug).

    ``get_by_user`` waits on a barrier sized for all N callers before
    returning — this forces the exact interleaving the old bug depended on:
    every caller reads the SAME unlocked, zero-failure row before any peer's
    failure is recorded (real concurrent HTTP calls to
    ``/internal/totp/verify`` race the same way, since this endpoint isn't
    serialized by signature-service's turno ``FOR UPDATE``).

    ``record_failure`` increments in one uninterrupted step (no ``await``
    between reading and writing the counter) — exactly the atomicity a real
    ``UPDATE ... RETURNING`` gets for free from Postgres's row lock. If
    ``record_failure`` were still the OLD read-then-write (deriving the new
    *absolute* value from the stale row each caller already holds instead of
    incrementing server-side), every one of the N callers here would compute
    ``0 + 1`` and the counter would end at 1, not N.
    """

    def __init__(self, snapshot: dict, n: int) -> None:
        self._snapshot = snapshot
        self._read_barrier = asyncio.Barrier(n)
        self.total_failures = 0
        self.locked_until: datetime | None = None

    async def fetchrow(self, query: str, *args: object) -> dict:
        if "WHERE user_id = $1 AND estado <> 'revocado'" in query:
            await self._read_barrier.wait()
            return {
                **self._snapshot,
                "failed_attempts": self.total_failures,
                "locked_until": self.locked_until,
            }
        if "failed_attempts = failed_attempts + 1" in query:
            _totp_id, max_attempts, lockout_seconds = args
            self.total_failures += 1
            if self.total_failures >= max_attempts and self.locked_until is None:
                self.locked_until = datetime.now(UTC) + timedelta(seconds=lockout_seconds)
            return {"failed_attempts": self.total_failures, "locked_until": self.locked_until}
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *args: object) -> str:
        raise AssertionError(f"unexpected execute on the failure path: {query}")


@pytest.mark.asyncio
async def test_verify_failed_attempts_atomic_under_concurrency() -> None:
    """D1 lost-update regression (see ``_ConcurrentFailureConn``): N
    concurrent failed TOTP verify attempts must each land their own
    increment — the counter must reach exactly ``totp_max_failed_attempts``
    (never fewer, e.g. stuck at 1 if every caller read the same stale base
    and wrote ``base + 1``) and lockout must trip at exactly that
    threshold. Once locked, later concurrent attempts correctly stop
    touching the counter at all (they see the now-locked row and short-
    circuit to ``bloqueado`` — that part of the flow is unchanged)."""
    secret = generate_secret()
    tenant_slug = "demo"
    ciphertext, nonce = encrypt_secret(
        secret,
        key=base64.b64decode(TEST_KEY),
        aad=build_aad(tenant_slug=tenant_slug, user_id=str(USER_ID)),
    )
    snapshot = {
        "id": TOTP_ID,
        "user_id": USER_ID,
        "secret_cifrado": ciphertext,
        "secret_nonce": nonce,
        "algoritmo": "SHA1",
        "digitos": 6,
        "periodo": 30,
        "estado": "activo",
        "last_timestep": 0,
        "key_version": 1,
        "created_at": datetime.now(UTC),
        "activated_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    n = settings.totp_max_failed_attempts + 2  # a couple of attempts past the threshold too
    conn = _ConcurrentFailureConn(snapshot, n)
    service = TotpService(conn)

    results = await asyncio.gather(
        *(service.verify(USER_ID, tenant_slug, "000000") for _ in range(n))
    )

    codigo_incorrecto = [r for r in results if r.motivo == "codigo_incorrecto"]
    bloqueado = [r for r in results if r.motivo == "bloqueado"]

    # Exactly `totp_max_failed_attempts` calls actually reached the counter —
    # no lost updates (which would have stalled this at 1) and no over-count
    # (once locked, later calls see the row already locked and never touch
    # the counter at all, so it can't run past the threshold either).
    assert conn.total_failures == settings.totp_max_failed_attempts
    assert len(codigo_incorrecto) == settings.totp_max_failed_attempts
    assert len(bloqueado) == n - settings.totp_max_failed_attempts
    assert all(r.intentos_restantes is not None and r.intentos_restantes >= 0 for r in codigo_incorrecto)
    # The attempt that tripped the threshold reports locked_until immediately
    # (derived from the same RETURNING row, not a second read).
    assert any(r.locked_until is not None for r in codigo_incorrecto)
    assert all(r.locked_until is not None for r in bloqueado)


@pytest.mark.asyncio
async def test_internal_verify_lost_race_reports_replay(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """classify_totp_attempt says 'valid' but the conditional UPDATE affects
    0 rows (a concurrent verify already advanced past it) → treated as replay,
    never as success (D1 atomicity guard)."""
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    conn.execute.return_value = "UPDATE 0"  # lost the race
    secret = generate_secret()
    code, _ = _valid_code(secret)
    active_row = _totp_row(secret=secret, estado="activo", last_timestep=0)
    failure_row = {"failed_attempts": 1, "locked_until": None}
    conn.fetchrow.side_effect = [_user_row(), active_row, failure_row]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": code},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valido"] is False
        assert body["motivo"] == "replay"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_no_active_credential_returns_no_activo(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    conn.fetchrow.side_effect = [_user_row(), None]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "123456"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valido"] is False
        assert body["motivo"] == "no_activo"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_unknown_user_returns_no_activo(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RBACRepository.get_user_by_keycloak_sub finds nothing → fail-closed,
    never a 500 — the caller (signature-service) treats this identically to
    'no TOTP configured'."""
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub-ghost", "tenant_slug": "demo", "username": "ghost"}
    )
    conn = _make_conn()
    conn.fetchrow.side_effect = [None]  # get_user_by_keycloak_sub → not provisioned
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "123456"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["motivo"] == "no_activo"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_missing_bearer_returns_401(client: AsyncClient) -> None:
    conn = _make_conn()  # dependency resolution still needs a conn, even though unused
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "123456"},
            headers={"X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "missing_token"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_lockout_honored_before_comparing_code(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    secret = generate_secret()
    locked_row = _totp_row(
        secret=secret,
        estado="activo",
        locked_until=datetime.now(UTC) + timedelta(minutes=15),
    )
    conn.fetchrow.side_effect = [_user_row(), locked_row]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "000000"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valido"] is False
        assert body["motivo"] == "bloqueado"
        assert body["locked_until"] is not None
    finally:
        _clear()


# ---------------------------------------------------------------------------
# AAD tenant_slug source parity — enroll must use the same source as verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_and_verify_derive_aad_from_same_bearer_claim(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: enroll's AESGCM AAD must be bound to the bearer's
    OWN ``tenant_slug`` JWT claim — the same source ``/internal/totp/verify``
    uses — not the ``X-Tenant-Slug`` header (which the gateway derives from
    that very claim, but which is a second, independent value once it
    reaches this service). The bearer's claim ("otro-tenant") deliberately
    differs from the header ("demo") here: under the old header-sourced
    enroll, this mismatch would make ``verify`` fail to decrypt (``InvalidTag``,
    surfaced as a 500) even though it's the SAME bearer used for both calls.
    """
    claims = {"user_id": "kc-sub", "tenant_slug": "otro-tenant", "username": "operador"}
    _mock_validate_token(monkeypatch, claims)

    captured: dict = {}

    async def _enroll_fetchrow(query: str, *args: object):
        if "WHERE user_id = $1 AND estado <> 'revocado'" in query:
            return None  # no existing credential — fresh enroll
        if query.strip().startswith("INSERT INTO auth_user_totp"):
            captured["secret_cifrado"] = args[1]
            captured["secret_nonce"] = args[2]
            return _totp_row(secret=generate_secret())  # only digitos/periodo are read from this
        if "FROM auth_users WHERE id = $1" in query:
            return _user_row()
        raise AssertionError(f"unexpected fetchrow: {query}")

    conn = _make_conn()
    conn.fetchrow = AsyncMock(side_effect=_enroll_fetchrow)
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll",
            json={},
            headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
        )
        assert resp.status_code == 201
    finally:
        _clear()

    otpauth_uri = resp.json()["otpauth_uri"]
    secret_b32 = urllib.parse.parse_qs(urllib.parse.urlparse(otpauth_uri).query)["secret"][0]
    raw_secret = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))

    stored_row = {
        "id": TOTP_ID,
        "user_id": USER_ID,
        "secret_cifrado": captured["secret_cifrado"],
        "secret_nonce": captured["secret_nonce"],
        "algoritmo": "SHA1",
        "digitos": 6,
        "periodo": 30,
        "estado": "activo",  # skip the activation round trip — only AAD parity matters here
        "last_timestep": 0,
        "failed_attempts": 0,
        "locked_until": None,
        "key_version": 1,
        "created_at": datetime.now(UTC),
        "activated_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    code, _ = _valid_code(raw_secret)

    conn2 = _make_conn()
    conn2.fetchrow.side_effect = [_user_row(), stored_row]
    _override_internal(conn2)
    try:
        resp2 = await client.post(
            "/internal/totp/verify",
            json={"code": code},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
    finally:
        _clear()

    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["valido"] is True
    assert body2["factor_tipo"] == "totp"


# ---------------------------------------------------------------------------
# GET /internal/totp/status — AREA-04-02 / AREA-04-05 (signature-service)
# ---------------------------------------------------------------------------



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("estado", "activo"),
    [("activo", True), ("pendiente_activacion", False)],
)
async def test_internal_totp_status_reports_active_and_owner(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    _internal_token: None,
    estado: str,
    activo: bool,
) -> None:
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    secret = generate_secret()
    conn.fetchrow.side_effect = [_user_row(), _totp_row(secret=secret, estado=estado)]
    conn.fetchval.return_value = 0  # recovery codes restantes
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/totp/status",
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"activo": activo, "user_id": str(USER_ID)}


@pytest.mark.asyncio
async def test_internal_totp_status_no_configurado(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _internal_token: None
) -> None:
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    conn.fetchrow.side_effect = [_user_row(), None]
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/totp/status",
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json() == {"activo": False, "user_id": str(USER_ID)}


@pytest.mark.asyncio
async def test_internal_totp_status_unprovisioned_user_is_inactive_without_owner(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _internal_token: None
) -> None:
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    conn.fetchrow.side_effect = [None]
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/totp/status",
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json() == {"activo": False, "user_id": None}


@pytest.mark.asyncio
async def test_internal_totp_status_requires_internal_token(
    client: AsyncClient, _internal_token: None
) -> None:
    conn = _make_conn()
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/totp/status", headers={"Authorization": "Bearer faketoken"}
        )
        bad = await client.get(
            "/internal/totp/status",
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": "otro"},
        )
    finally:
        _clear()
    assert resp.status_code == 401
    assert bad.status_code in (401, 403)


@pytest.mark.asyncio
async def test_internal_totp_status_missing_bearer_returns_401_even_with_internal_token(
    client: AsyncClient, _internal_token: None
) -> None:
    """El token de servicio NO sustituye a la identidad: sin bearer no hay dueño."""
    conn = _make_conn()
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/totp/status", headers={"X-Internal-Token": _STATUS_TOKEN}
        )
    finally:
        _clear()
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_token"
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_totp_status_without_tenant_claim_is_inactive_and_never_touches_db(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _internal_token: None
) -> None:
    """Fail-closed: sin tenant en el JWT no se adivina schema ni se consulta nada."""
    _mock_validate_token(monkeypatch, {"user_id": "kc-sub", "username": "operador"})
    conn = _make_conn()
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/totp/status",
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json() == {"activo": False, "user_id": None}
    conn.fetchrow.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_verify_invalid_code_does_not_attribute_an_owner(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``user_id`` solo acompaña a un factor VÁLIDO (AREA-04-05): un código
    incorrecto no debe revelar a quién pertenece el bearer."""
    _mock_validate_token(
        monkeypatch, {"user_id": "kc-sub", "tenant_slug": "demo", "username": "operador"}
    )
    conn = _make_conn()
    secret = generate_secret()
    conn.fetchrow.side_effect = [
        _user_row(),
        _totp_row(secret=secret, estado="activo", last_timestep=0, failed_attempts=0),
        {"failed_attempts": 1, "locked_until": None},
    ]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "000000"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": _STATUS_TOKEN},
        )
    finally:
        _clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["valido"] is False
    assert body.get("user_id") is None


# ---------------------------------------------------------------------------
# POST /internal/totp/verify — gate D-02 (X-Internal-Token), antes de tocar el bearer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_verify_without_internal_token_returns_401(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    validate = AsyncMock(return_value={"user_id": "kc-sub", "tenant_slug": "demo"})
    monkeypatch.setattr("app.routers.totp.validate_token", validate)
    _override_internal(_make_conn())
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "123456"},
            headers={"Authorization": "Bearer faketoken"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "missing_internal_token"
        validate.assert_not_called()  # el gate precede a cualquier trabajo
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_wrong_internal_token_returns_403(
    client: AsyncClient,
) -> None:
    _override_internal(_make_conn())
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "123456"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": "otro"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "invalid_internal_token"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_internal_verify_unconfigured_token_returns_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "signing_key_internal_token", "")
    _override_internal(_make_conn())
    try:
        resp = await client.post(
            "/internal/totp/verify",
            json={"code": "123456"},
            headers={"Authorization": "Bearer faketoken", "X-Internal-Token": "x"},
        )
        assert resp.status_code == 503
    finally:
        _clear()
