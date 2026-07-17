# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests — firma electrónica PERSONAL PKI por-usuario (E17/F4, Incremento 1).

Mismo estilo de conexión asyncpg mockeada que ``tests/test_totp.py``: sin
Postgres real, ``get_caller``/``get_tenant_conn``/``get_db_conn``
sobreescritos directamente. La criptografía (RSA, AESGCM, CSR/certificados
X.509) se ejercita DE VERDAD — solo la capa de BD está mockeada.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from base64 import b64decode, b64encode
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import NameOID
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.config import settings
from app.core.database import get_db_conn, get_tenant_conn
from app.core.signing_keys import (
    build_aad,
    build_csr,
    build_subject_name,
    csr_to_pem,
    decrypt_private_key,
    encrypt_private_key,
    generate_keypair,
    private_key_to_pem,
    sign_digest,
)
from app.main import app

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
USER_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_USER_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
KEY_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
TEST_KEK = base64.b64encode(b"1" * 32).decode()
INTERNAL_TOKEN = "test-internal-token"


class _FakeTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "signing_key_encryption_key", TEST_KEK)


@pytest.fixture(autouse=True)
def _internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "signing_key_internal_token", INTERNAL_TOKEN)


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_append = AsyncMock(return_value="fakehash")
    monkeypatch.setattr("app.services.signing_key_service._audit.append", mock_append)
    monkeypatch.setattr("app.services.totp_service._audit.append", mock_append)
    return mock_append


def _caller(user_id: uuid.UUID = USER_ID) -> Caller:
    # S2: /me/signing-key/enroll ahora exige PERM_FIRMA (require_permission)
    # — el caller "normal" de este archivo lo trae por defecto; los tests
    # del gate en sí usan un caller aparte sin el permiso.
    return Caller(
        user_id=user_id, keycloak_sub="kc-sub", is_root=False, permissions={"PERM_FIRMA": 3}
    )


def _make_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    conn.execute.return_value = "UPDATE 1"
    conn.fetch.return_value = []  # _resolve_cargo: sin grupos por defecto
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


def _user_row(user_id: uuid.UUID = USER_ID) -> dict:
    return {
        "id": user_id,
        "username": "OPERADOR",
        "email": "operador@demo.orpycamcp.local",
        "keycloak_sub": "kc-sub",
        "is_root": False,
        "is_active": True,
    }


def _aad(tenant_slug: str = "demo", user_id: uuid.UUID = USER_ID) -> bytes:
    return build_aad(tenant_slug=tenant_slug, user_id=str(user_id))


def _issue_test_cert(
    private_key: rsa.RSAPrivateKey, *, cn: str = "OPERADOR"
) -> x509.Certificate:
    """Emisor OFFLINE simulado: CA de test en memoria (NO
    ``scripts/gen-user-cert.sh``) — el flujo enroll→activo→firma debe ser
    testeable end-to-end en pytest sin depender de un script shell."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ca-usuarios-test")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .sign(ca_key, hashes.SHA256())
    )


def _key_row(
    *,
    private_key: rsa.RSAPrivateKey | None = None,
    estado: str = "pendiente_emision",
    cert_pem: str | None = None,
    serial: str | None = None,
    tenant_slug: str = "demo",
    user_id: uuid.UUID = USER_ID,
) -> dict:
    private_key = private_key or generate_keypair()
    private_key_pem = private_key_to_pem(private_key)
    ciphertext, nonce = encrypt_private_key(
        private_key_pem, key=base64.b64decode(TEST_KEK), aad=_aad(tenant_slug, user_id)
    )
    subject = build_subject_name(username="OPERADOR", email="operador@demo.test", cargo=None)
    csr_pem = csr_to_pem(build_csr(private_key, subject)).decode()
    return {
        "id": KEY_ID,
        "user_id": user_id,
        "cert_pem": cert_pem,
        "csr_pem": csr_pem,
        "private_key_cifrado": ciphertext,
        "private_key_nonce": nonce,
        "subject_dn": subject.rfc4514_string(),
        "cargo": None,
        "serial": serial,
        "not_before": datetime.now(UTC) - timedelta(days=1),
        "not_after": datetime.now(UTC) + timedelta(days=825),
        "key_version": 1,
        "estado": estado,
        "created_at": datetime.now(UTC),
        "activated_at": datetime.now(UTC) if estado == "activo" else None,
        "updated_at": datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# Paso 0 sanity — core AESGCM round trip + AAD (mismo patrón que TOTP)
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip() -> None:
    key = base64.b64decode(TEST_KEK)
    pem = private_key_to_pem(generate_keypair())
    aad = _aad()
    ciphertext, nonce = encrypt_private_key(pem, key=key, aad=aad)
    assert decrypt_private_key(ciphertext, nonce=nonce, key=key, aad=aad) == pem


def test_decrypt_fails_with_aad_from_another_user() -> None:
    key = base64.b64decode(TEST_KEK)
    pem = private_key_to_pem(generate_keypair())
    ciphertext, nonce = encrypt_private_key(pem, key=key, aad=_aad(user_id=USER_ID))
    with pytest.raises(InvalidTag):
        decrypt_private_key(ciphertext, nonce=nonce, key=key, aad=_aad(user_id=OTHER_USER_ID))


def test_decrypt_fails_with_aad_from_another_tenant() -> None:
    key = base64.b64decode(TEST_KEK)
    pem = private_key_to_pem(generate_keypair())
    ciphertext, nonce = encrypt_private_key(pem, key=key, aad=_aad(tenant_slug="demo"))
    with pytest.raises(InvalidTag):
        decrypt_private_key(ciphertext, nonce=nonce, key=key, aad=_aad(tenant_slug="otro"))


def test_sign_digest_produces_verifiable_pkcs1v15_signature() -> None:
    private_key = generate_keypair()
    digest = hashlib.sha256(b"contenido de prueba").digest()
    signature = sign_digest(private_key, digest)
    private_key.public_key().verify(
        signature, digest, padding.PKCS1v15(), Prehashed(hashes.SHA256())
    )  # no exception == válida


# ---------------------------------------------------------------------------
# Enroll — nunca devuelve la clave privada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_fresh_returns_csr_never_private_key(client: AsyncClient) -> None:
    conn = _make_conn()
    # get_by_user, rbac.get_user, insert_pending (RETURNING — valor no usado
    # por el service, solo el CSR calculado localmente se devuelve).
    conn.fetchrow.side_effect = [None, _user_row(), _key_row()]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/signing-key/enroll", json={}, headers=TENANT_HEADERS
        )
    finally:
        _clear()

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["estado"] == "pendiente_emision"
    assert "BEGIN CERTIFICATE REQUEST" in body["csr_pem"]
    raw = resp.text
    assert "PRIVATE KEY" not in raw  # REGLA no-fuga: nunca la clave privada

    # insert_pending fue llamado con la clave YA cifrada — no en claro.
    insert_call = conn.fetchrow.await_args_list[-1]
    assert "INSERT INTO auth_user_signing_key" in insert_call.args[0]


@pytest.mark.asyncio
async def test_enroll_replaces_pending_without_step_up(client: AsyncClient) -> None:
    """A diferencia de TOTP, el re-enroll NO exige step-up — la nueva clave
    nace ``pendiente_emision`` (inútil hasta la emisión offline), así que no
    hay capacidad de firma que un secuestro de sesión pudiera robar de
    inmediato con solo re-enrolar."""
    conn = _make_conn()
    existing = _key_row(estado="pendiente_emision")
    conn.fetchrow.side_effect = [existing, _user_row(), _key_row()]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/signing-key/enroll", json={}, headers=TENANT_HEADERS
        )
    finally:
        _clear()

    assert resp.status_code == 201, resp.text
    # revoke() de la fila vieja fue llamado.
    revoke_calls = [c for c in conn.execute.await_args_list if "revocado" in c.args[0]]
    assert len(revoke_calls) == 1
    assert revoke_calls[0].args[1] == existing["id"]


@pytest.mark.asyncio
async def test_enroll_requires_perm_firma(client: AsyncClient) -> None:
    """S2: sin ``PERM_FIRMA`` el enroll se rechaza ANTES de tocar la BD del
    servicio — evita el self-DoS de un llamante sin permiso de firma
    re-enrolando (y revocando atómicamente) la clave `activo` de otro."""
    conn = _make_conn()
    no_perm_caller = Caller(
        user_id=USER_ID, keycloak_sub="kc-sub", is_root=False, permissions={}
    )
    _override_public(conn, caller=no_perm_caller)
    try:
        resp = await client.post(
            "/api/v1/auth/me/signing-key/enroll", json={}, headers=TENANT_HEADERS
        )
    finally:
        _clear()

    assert resp.status_code == 403, resp.text
    # No se llegó a tocar la credencial de firma: ni get_by_user, ni
    # insert_pending, ni revoke.
    assert conn.fetchrow.await_count == 0


@pytest.mark.asyncio
async def test_enroll_with_perm_firma_succeeds(client: AsyncClient) -> None:
    """Con ``PERM_FIRMA`` el enroll procede normalmente (mismo camino feliz
    que :func:`test_enroll_fresh_returns_csr_never_private_key`, cubierto
    aquí de forma explícita para dejar constancia del gate positivo)."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [None, _user_row(), _key_row()]
    firmante = Caller(
        user_id=USER_ID, keycloak_sub="kc-sub", is_root=False, permissions={"PERM_FIRMA": 3}
    )
    _override_public(conn, caller=firmante)
    try:
        resp = await client.post(
            "/api/v1/auth/me/signing-key/enroll", json={}, headers=TENANT_HEADERS
        )
    finally:
        _clear()

    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Status — nunca la clave privada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_no_configurado(client: AsyncClient) -> None:
    conn = _make_conn()
    conn.fetchrow.side_effect = [None]
    _override_public(conn)
    try:
        resp = await client.get("/api/v1/auth/me/signing-key", headers=TENANT_HEADERS)
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json()["estado"] == "no_configurado"


@pytest.mark.asyncio
async def test_status_never_includes_private_key(client: AsyncClient) -> None:
    conn = _make_conn()
    row = _key_row(estado="activo", cert_pem="-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n", serial="ab12")
    conn.fetchrow.side_effect = [row]
    _override_public(conn)
    try:
        resp = await client.get("/api/v1/auth/me/signing-key", headers=TENANT_HEADERS)
    finally:
        _clear()
    assert resp.status_code == 200
    assert "PRIVATE KEY" not in resp.text
    assert "cifrado" not in resp.text.lower()
    body = resp.json()
    assert body["estado"] == "activo"
    assert body["serial"] == "ab12"


# ---------------------------------------------------------------------------
# Flujo end-to-end: enroll -> activate (CA de test en memoria) -> sign-digest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_rejects_certificate_not_matching_csr(client: AsyncClient) -> None:
    conn = _make_conn()
    pending = _key_row(estado="pendiente_emision")
    # Certificado emitido para OTRA clave (no la del CSR de esta fila).
    other_key = generate_keypair()
    foreign_cert = _issue_test_cert(other_key)
    cert_pem = foreign_cert.public_bytes(serialization.Encoding.PEM).decode()

    conn.fetchrow.side_effect = [pending]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/activate",
            json={"user_id": str(USER_ID), "cert_pem": cert_pem},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "X-Tenant-Slug": "demo"},
        )
    finally:
        _clear()
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "certificado_no_corresponde_al_csr"


@pytest.mark.asyncio
async def test_activate_success_matching_csr(client: AsyncClient) -> None:
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()

    activated = dict(pending)
    activated.update(
        estado="activo",
        cert_pem=cert_pem,
        serial=format(cert.serial_number, "x"),
        activated_at=datetime.now(UTC),
    )
    conn.fetchrow.side_effect = [pending, activated]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/activate",
            json={"user_id": str(USER_ID), "cert_pem": cert_pem},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "X-Tenant-Slug": "demo"},
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "activo"


@pytest.mark.asyncio
async def test_activate_leaves_audit_trail(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    """S1: la transición pendiente_emision -> activo (la credencial se
    vuelve FIRMABLE con valor legal) ahora deja un asiento
    ``signing_key.activada`` — antes no dejaba traza alguna (ni el service
    ni el router la emitían)."""
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    serial = format(cert.serial_number, "x")

    activated = dict(pending)
    activated.update(
        estado="activo", cert_pem=cert_pem, serial=serial, activated_at=datetime.now(UTC)
    )
    conn.fetchrow.side_effect = [pending, activated]
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/activate",
            json={"user_id": str(USER_ID), "cert_pem": cert_pem},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "X-Tenant-Slug": "demo"},
        )
    finally:
        _clear()

    assert resp.status_code == 200, resp.text
    assert _patch_audit.await_count == 1
    kwargs = _patch_audit.await_args.kwargs
    assert kwargs["action"] == "signing_key.activada"
    assert kwargs["object_type"] == "signing_key"
    assert kwargs["object_ref"] == str(USER_ID)
    assert kwargs["tenant_slug"] == "demo"
    assert kwargs["canal"] == "system"
    assert kwargs["payload"]["serial"] == serial
    assert "certificado_fingerprint" in kwargs["payload"]
    # REGLA no-fuga: ni la clave privada ni su ciphertext viajan al asiento.
    assert "private_key" not in str(kwargs["payload"]).lower()


@pytest.mark.asyncio
async def test_activate_aborts_when_audit_fails(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    """S1 (atomicidad): a diferencia de ``_audit_event`` (best-effort, usado
    por ``enroll``/``load_and_sign_digest``), el asiento de
    ``activar_certificado`` vive DENTRO de la misma transacción que el
    ``UPDATE`` — un fallo del asiento debe abortar la activación en vez de
    tragarse en silencio. Con ``conn`` mockeada esta prueba solo puede
    demostrar la mitad observable (el fallo PROPAGA, no se traga); que el
    ``UPDATE`` también se revierte en Postgres real pertenece al carril de
    integración contra BD real (piloto en archive-service, aún no extendido
    a auth-service)."""
    _patch_audit.side_effect = RuntimeError("audit_log unavailable")
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    activated = dict(pending)
    activated.update(estado="activo", cert_pem=cert_pem, serial=format(cert.serial_number, "x"))
    conn.fetchrow.side_effect = [pending, activated]
    _override_internal(conn)
    try:
        # Misma técnica que test_enroll_fails_closed_without_kek: ASGITransport
        # re-lanza excepciones no manejadas del app en vez de devolver un 500
        # inspeccionable en resp.status_code.
        with pytest.raises(RuntimeError, match="audit_log unavailable"):
            await client.post(
                "/internal/signing-key/activate",
                json={"user_id": str(USER_ID), "cert_pem": cert_pem},
                headers={"X-Internal-Token": INTERNAL_TOKEN, "X-Tenant-Slug": "demo"},
            )
    finally:
        _clear()


@pytest.mark.asyncio
async def test_activate_requires_internal_token(client: AsyncClient) -> None:
    conn = _make_conn()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/activate",
            json={"user_id": str(USER_ID), "cert_pem": "x"},
            headers={"X-Tenant-Slug": "demo"},
        )
    finally:
        _clear()
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /internal/signing-key/status — consultado por CadenaFirmaService.firmar()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_status_resolves_from_bearer(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.signing_key.validate_token",
        AsyncMock(return_value={"user_id": "kc-sub", "tenant_slug": "demo"}),
    )
    conn = _make_conn()
    active_row = _key_row(estado="activo", cert_pem="-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n")
    conn.fetchrow.side_effect = [_user_row(), active_row]
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/signing-key/status",
            headers={"X-Internal-Token": INTERNAL_TOKEN, "Authorization": "Bearer fake"},
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "activo"
    assert "PRIVATE KEY" not in resp.text


@pytest.mark.asyncio
async def test_internal_status_requires_internal_token(client: AsyncClient) -> None:
    conn = _make_conn()
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/signing-key/status", headers={"Authorization": "Bearer fake"}
        )
    finally:
        _clear()
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# sign-digest — identidad SIEMPRE del Bearer, TOTP verify-and-consume atómico
# ---------------------------------------------------------------------------


def _mock_validate_token(monkeypatch: pytest.MonkeyPatch, claims: dict) -> None:
    monkeypatch.setattr(
        "app.routers.signing_key.validate_token", AsyncMock(return_value=claims)
    )


def _claims(user_id: str = "kc-sub", tenant_slug: str = "demo") -> dict:
    return {"user_id": user_id, "tenant_slug": tenant_slug}


@pytest.mark.asyncio
async def test_sign_digest_success(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_validate_token(monkeypatch, _claims())
    conn = _make_conn()
    private_key = generate_keypair()
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    active_key_row = _key_row(private_key=private_key, estado="activo", cert_pem=cert_pem)

    active_totp_secret = b"0123456789012345678901"
    from app.core.totp import _hotp, build_aad as build_totp_aad, current_timestep, encrypt_secret

    code = _hotp(active_totp_secret, current_timestep())
    monkeypatch.setattr(settings, "totp_encryption_key", TEST_KEK)
    totp_ciphertext, totp_nonce = encrypt_secret(
        active_totp_secret,
        key=base64.b64decode(TEST_KEK),
        aad=build_totp_aad(tenant_slug="demo", user_id=str(USER_ID)),
    )

    totp_row = {
        "id": uuid.uuid4(),
        "user_id": USER_ID,
        "secret_cifrado": totp_ciphertext,
        "secret_nonce": totp_nonce,
        "algoritmo": "SHA1",
        "digitos": 6,
        "periodo": 30,
        "estado": "activo",
        "last_timestep": current_timestep() - 1,
        "failed_attempts": 0,
        "locked_until": None,
        "key_version": 1,
        "created_at": datetime.now(UTC),
        "activated_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }

    # get_user_by_keycloak_sub, get_by_user_for_update (signing key),
    # TotpRepository.get_by_user (dentro de verify), advance_timestep no usa
    # fetchrow (usa execute) -> siguiente fetchrow es None si lo hubiera.
    conn.fetchrow.side_effect = [_user_row(), active_key_row, totp_row]
    conn.execute.side_effect = lambda *a, **kw: "UPDATE 1"

    digest = hashlib.sha256(b"C14N(SignedInfo) de prueba").digest()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/sign-digest",
            json={
                "digest_b64": b64encode(digest).decode(),
                "alg": "RSA-SHA256",
                "prehashed": True,
                "totp_code": code,
            },
            headers={
                "X-Internal-Token": INTERNAL_TOKEN,
                "Authorization": "Bearer fake.jwt.token",
            },
        )
    finally:
        _clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    signature = b64decode(body["signature_value_b64"])
    private_key.public_key().verify(
        signature, digest, padding.PKCS1v15(), Prehashed(hashes.SHA256())
    )
    assert body["certificado_pem"] == cert_pem
    assert "PRIVATE KEY" not in resp.text


@pytest.mark.asyncio
async def test_sign_digest_no_active_key_returns_422(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_validate_token(monkeypatch, _claims())
    conn = _make_conn()
    conn.fetchrow.side_effect = [_user_row(), None]  # get_user, get_by_user_for_update
    digest = hashlib.sha256(b"x").digest()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/sign-digest",
            json={"digest_b64": b64encode(digest).decode(), "totp_code": "000000"},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "Authorization": "Bearer fake"},
        )
    finally:
        _clear()
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "signing_key_no_activa"


@pytest.mark.asyncio
async def test_sign_digest_wrong_totp_never_signs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un TOTP inválido rechaza SIN firmar — la clave nunca se descifra ni se
    usa (verificado indirectamente: la respuesta nunca lleva signature_value_b64)."""
    _mock_validate_token(monkeypatch, _claims())
    conn = _make_conn()
    private_key = generate_keypair()
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    active_key_row = _key_row(private_key=private_key, estado="activo", cert_pem=cert_pem)

    from app.core.totp import build_aad as build_totp_aad, encrypt_secret  # noqa: PLC0415

    totp_ciphertext, totp_nonce = encrypt_secret(
        b"0123456789012345678901",
        key=base64.b64decode(TEST_KEK),
        aad=build_totp_aad(tenant_slug="demo", user_id=str(USER_ID)),
    )
    monkeypatch.setattr(settings, "totp_encryption_key", TEST_KEK)
    totp_row = {
        "id": uuid.uuid4(),
        "user_id": USER_ID,
        "secret_cifrado": totp_ciphertext,
        "secret_nonce": totp_nonce,
        "algoritmo": "SHA1",
        "digitos": 6,
        "periodo": 30,
        "estado": "activo",
        "last_timestep": 0,
        "failed_attempts": 0,
        "locked_until": None,
        "key_version": 1,
        "created_at": datetime.now(UTC),
        "activated_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    failure_row = {"failed_attempts": 1, "locked_until": None}
    conn.fetchrow.side_effect = [_user_row(), active_key_row, totp_row, failure_row]

    digest = hashlib.sha256(b"x").digest()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/sign-digest",
            json={"digest_b64": b64encode(digest).decode(), "totp_code": "000000"},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "Authorization": "Bearer fake"},
        )
    finally:
        _clear()
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"] == "totp_incorrecto"
    assert "signature_value_b64" not in resp.text


@pytest.mark.asyncio
async def test_sign_digest_identity_from_bearer_ignores_body(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``InternalSignDigestRequest`` no tiene campo ``user_id`` — un body que
    intente colar uno es simplemente ignorado por Pydantic (extra="ignore"
    por defecto): la identidad SIEMPRE se deriva del bearer (D1, cierra IDOR).
    Este test confirma que un ``user_id`` ajeno en el body no cambia a quién
    se resuelve (se sigue resolviendo por ``keycloak_sub`` del bearer)."""
    _mock_validate_token(monkeypatch, _claims(user_id="kc-sub"))
    conn = _make_conn()
    conn.fetchrow.side_effect = [_user_row(user_id=USER_ID), None]

    digest = hashlib.sha256(b"x").digest()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/sign-digest",
            json={
                "digest_b64": b64encode(digest).decode(),
                "totp_code": "000000",
                "user_id": str(OTHER_USER_ID),  # campo espurio, debe ser ignorado
            },
            headers={"X-Internal-Token": INTERNAL_TOKEN, "Authorization": "Bearer fake"},
        )
    finally:
        _clear()
    # get_by_user_for_update fue llamado con USER_ID (resuelto por el
    # keycloak_sub del bearer), NO con OTHER_USER_ID del body.
    for_update_calls = [
        c for c in conn.fetchrow.await_args_list if "FOR UPDATE" in c.args[0]
    ]
    assert len(for_update_calls) == 1
    assert for_update_calls[0].args[1] == USER_ID
    assert resp.status_code == 422  # no_activo (no hay fila) — pero por el user_id correcto


@pytest.mark.asyncio
async def test_sign_digest_requires_internal_token(client: AsyncClient) -> None:
    conn = _make_conn()
    digest = hashlib.sha256(b"x").digest()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/sign-digest",
            json={"digest_b64": b64encode(digest).decode(), "totp_code": "000000"},
            headers={"Authorization": "Bearer fake"},
        )
    finally:
        _clear()
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sign_digest_requires_bearer(client: AsyncClient) -> None:
    conn = _make_conn()
    digest = hashlib.sha256(b"x").digest()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/sign-digest",
            json={"digest_b64": b64encode(digest).decode(), "totp_code": "000000"},
            headers={"X-Internal-Token": INTERNAL_TOKEN},
        )
    finally:
        _clear()
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Fail-closed sin KEK configurada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_fails_closed_without_kek(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin ``signing_key_encryption_key`` configurada, el enroll falla
    cerrado — NUNCA cae a una clave implícita/derivada. ``httpx``'s
    ``ASGITransport`` re-lanza excepciones no manejadas del app (mismo
    comportamiento que ``ServerErrorMiddleware`` en producción, que además
    responde 500 al cliente real) — se afirma con ``pytest.raises`` en vez de
    inspeccionar ``resp.status_code``, ver la nota de ``totp_service`` /
    ``config.py`` para el mismo criterio fail-closed sin valor implícito."""
    monkeypatch.setattr(settings, "signing_key_encryption_key", "")
    conn = _make_conn()
    conn.fetchrow.side_effect = [None, _user_row()]
    _override_public(conn)
    try:
        with pytest.raises(RuntimeError, match="signing_key_encryption_key"):
            await client.post(
                "/api/v1/auth/me/signing-key/enroll", json={}, headers=TENANT_HEADERS
            )
    finally:
        _clear()
