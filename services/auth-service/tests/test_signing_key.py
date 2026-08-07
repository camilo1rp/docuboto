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


def _generate_sub_ca(cn: str = "ca-usuarios-test") -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """CA de usuarios de test en memoria — ``basicConstraints CA:TRUE`` +
    ``keyUsage keyCertSign`` (Incremento 2, ``activar_certificado`` los
    exige vía ``leaf.verify_directly_issued_by(sub_ca)``). Mismo espíritu
    que ``scripts/gen-user-cert.sh`` pero sin tocar el filesystem/openssl."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return ca_key, ca_cert


@pytest.fixture
def sub_ca(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Monta la sub-CA de usuarios de test (SOLO su certificado PÚBLICO,
    nunca ``ca_key``) vía ``settings.signing_key_ca_users_cert_path`` — mismo
    contrato que ``ca-users.crt`` en producción. Usado por los tests que
    ejercitan el anclaje de :meth:`SigningKeyService.activar_certificado`
    (Incremento 2, Baja #4)."""
    ca_key, ca_cert = _generate_sub_ca()
    cert_path = tmp_path / "ca-users.crt"
    cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(settings, "signing_key_ca_users_cert_path", str(cert_path))
    return ca_key, ca_cert


def _issue_test_cert(
    private_key: rsa.RSAPrivateKey,
    *,
    cn: str = "OPERADOR",
    ca_key: rsa.RSAPrivateKey | None = None,
    ca_cert: x509.Certificate | None = None,
) -> x509.Certificate:
    """Emisor OFFLINE simulado: CA de test en memoria (NO
    ``scripts/gen-user-cert.sh``) — el flujo enroll→activo→firma debe ser
    testeable end-to-end en pytest sin depender de un script shell.

    ``ca_key``/``ca_cert`` (Incremento 2): cuando se pasan (típicamente el
    fixture ``sub_ca``), el leaf se emite con ESA CA — necesario para que
    ``activar_certificado`` lo acepte (exige encadenar con la sub-CA
    MONTADA). Sin ellos, genera una CA ad-hoc DISTINTA en cada llamada —
    sigue sirviendo para probar caminos que fallan ANTES del chequeo de
    cadena (p. ej. ``certificado_no_corresponde_al_csr``, o certificados
    usados fuera de ``activar_certificado`` como en ``sign-digest``)."""
    if ca_key is None or ca_cert is None:
        ca_key, ca_cert = _generate_sub_ca()
    now = datetime.now(UTC)
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
    certificado_fingerprint: str | None = None,
    user_ca_fingerprint: str | None = None,
    revocado_at: datetime | None = None,
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
        # Incremento 2 (Baja #4) — revocación + anclaje de la sub-CA.
        "revocado_at": revocado_at,
        "revocado_por": None,
        "motivo_revocacion": None,
        "certificado_fingerprint": certificado_fingerprint,
        "user_ca_fingerprint": user_ca_fingerprint,
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
async def test_activate_success_matching_csr(
    client: AsyncClient, sub_ca: tuple[rsa.RSAPrivateKey, x509.Certificate]
) -> None:
    ca_key, ca_cert = sub_ca
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key, ca_key=ca_key, ca_cert=ca_cert)
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

    # Incremento 2 (Baja #4): activate() persiste AMBAS huellas — args[0] es
    # el SQL, así que args[6]/args[7] son $6/$7 (certificado_fingerprint,
    # user_ca_fingerprint — ver SigningKeyRepository.activate).
    activate_call = next(
        c for c in conn.fetchrow.await_args_list if "SET estado = 'activo'" in c.args[0]
    )
    assert activate_call.args[6] == cert.fingerprint(hashes.SHA256()).hex()
    assert activate_call.args[7] == ca_cert.fingerprint(hashes.SHA256()).hex()


@pytest.mark.asyncio
async def test_activate_rejects_leaf_not_issued_by_mounted_sub_ca(
    client: AsyncClient, sub_ca: tuple[rsa.RSAPrivateKey, x509.Certificate]
) -> None:
    """Incremento 2 (Baja #4), fail-closed: el leaf corresponde REALMENTE al
    CSR (misma clave pública — pasa ese chequeo), pero fue emitido por una
    CA DISTINTA de la sub-CA montada en ``signing_key_ca_users_cert_path``
    — la activación se rechaza (422), nunca queda ``activo``."""
    _ca_key, _ca_cert = sub_ca  # monta la sub-CA de test — pero el leaf usa OTRA
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    # Emitido por una CA ad-hoc DISTINTA de la montada (sub_ca no se pasa).
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()

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
    assert resp.json()["detail"]["error"] == "certificado_no_emitido_por_sub_ca"


@pytest.mark.asyncio
async def test_activate_fails_closed_without_sub_ca_configured(client: AsyncClient) -> None:
    """Sin ``signing_key_ca_users_cert_path`` configurada (default de
    prueba, sin el fixture ``sub_ca``), la activación falla cerrado (503) —
    NUNCA activa una credencial sin poder verificar contra qué CA."""
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()

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
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error"] == "sub_ca_usuarios_no_configurada"


@pytest.mark.asyncio
async def test_activate_leaves_audit_trail(
    client: AsyncClient,
    _patch_audit: AsyncMock,
    sub_ca: tuple[rsa.RSAPrivateKey, x509.Certificate],
) -> None:
    """S1: la transición pendiente_emision -> activo (la credencial se
    vuelve FIRMABLE con valor legal) ahora deja un asiento
    ``signing_key.activada`` — antes no dejaba traza alguna (ni el service
    ni el router la emitían)."""
    ca_key, ca_cert = sub_ca
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key, ca_key=ca_key, ca_cert=ca_cert)
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
    assert kwargs["payload"]["user_ca_fingerprint"] == ca_cert.fingerprint(hashes.SHA256()).hex()
    # REGLA no-fuga: ni la clave privada ni su ciphertext viajan al asiento.
    assert "private_key" not in str(kwargs["payload"]).lower()


@pytest.mark.asyncio
async def test_activate_aborts_when_audit_fails(
    client: AsyncClient,
    _patch_audit: AsyncMock,
    sub_ca: tuple[rsa.RSAPrivateKey, x509.Certificate],
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
    ca_key, ca_cert = sub_ca
    _patch_audit.side_effect = RuntimeError("audit_log unavailable")
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key, ca_key=ca_key, ca_cert=ca_cert)
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
async def test_activate_fails_closed_without_audit_lib(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    sub_ca: tuple[rsa.RSAPrivateKey, x509.Certificate],
) -> None:
    """R1 (Baja de auditoría #1, Incremento 2): si ``orpycamcp_common`` no
    está instalado, ``activar_certificado`` se REHÚSA a activar sin traza —
    antes el asiento ``signing_key.activada`` simplemente se saltaba
    (``if _AUDIT_AVAILABLE:`` sin ``else``) y la activación seguía
    adelante. Ahora falla ANTES de tocar la fila (ni ``activate()`` ni
    ``verify_directly_issued_by`` se alcanzan)."""
    monkeypatch.setattr("app.services.signing_key_service._AUDIT_AVAILABLE", False)
    ca_key, ca_cert = sub_ca
    conn = _make_conn()
    private_key = generate_keypair()
    pending = _key_row(estado="pendiente_emision", private_key=private_key)
    cert = _issue_test_cert(private_key, ca_key=ca_key, ca_cert=ca_cert)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
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
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error"] == "auditoria_no_disponible"
    # Solo get_by_user — nunca llega a activate() (ni a verificar la cadena).
    assert conn.fetchrow.await_count == 1


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
    from app.core.totp import _hotp, current_timestep, encrypt_secret
    from app.core.totp import build_aad as build_totp_aad

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

    from app.core.totp import build_aad as build_totp_aad  # noqa: PLC0415
    from app.core.totp import encrypt_secret

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


# ---------------------------------------------------------------------------
# E17/F4, Incremento 2 — revocación + step-up en enroll/re-enroll
# ---------------------------------------------------------------------------


def _totp_row_and_code(
    monkeypatch: pytest.MonkeyPatch, *, user_id: uuid.UUID = USER_ID, tenant_slug: str = "demo"
) -> tuple[dict, str]:
    """Fila TOTP ``activo`` + código RFC 6238 válido para ESA fila — mismo
    patrón que :func:`test_sign_digest_success`, factorizado para el
    step-up de enroll/revoke de Incremento 2."""
    from app.core.totp import _hotp, current_timestep, encrypt_secret
    from app.core.totp import build_aad as build_totp_aad

    secret = b"0123456789012345678901"
    code = _hotp(secret, current_timestep())
    monkeypatch.setattr(settings, "totp_encryption_key", TEST_KEK)
    ciphertext, nonce = encrypt_secret(
        secret,
        key=base64.b64decode(TEST_KEK),
        aad=build_totp_aad(tenant_slug=tenant_slug, user_id=str(user_id)),
    )
    row = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "secret_cifrado": ciphertext,
        "secret_nonce": nonce,
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
    return row, code


@pytest.mark.asyncio
async def test_enroll_reenroll_active_requires_step_up(client: AsyncClient) -> None:
    """Incremento 2: re-enrolar sobre una credencial ``activo`` sin
    ``step_up_totp_code`` se rechaza ANTES de tocar TOTP/revocar — cierra el
    residual de Incremento 1 (un re-enroll revocaba la clave activa sin
    ningún gate propio)."""
    conn = _make_conn()
    existing = _key_row(estado="activo")
    conn.fetchrow.side_effect = [existing, _user_row()]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/signing-key/enroll", json={}, headers=TENANT_HEADERS
        )
    finally:
        _clear()
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "signing_key_activa_requiere_step_up"
    # No se intentó revocar ni verificar TOTP alguno.
    revoke_calls = [c for c in conn.execute.await_args_list if "revocado" in c.args[0]]
    assert len(revoke_calls) == 0


@pytest.mark.asyncio
async def test_enroll_reenroll_active_with_valid_step_up_succeeds(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _make_conn()
    existing = _key_row(estado="activo")
    totp_row, code = _totp_row_and_code(monkeypatch)
    # get_by_user (signing key), rbac.get_user, TotpRepository.get_by_user
    # (dentro de verify), insert_pending.
    conn.fetchrow.side_effect = [existing, _user_row(), totp_row, _key_row()]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/signing-key/enroll",
            json={"step_up_totp_code": code},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 201, resp.text
    # Filtro específico ("SET estado = 'revocado'"): la propia verificación
    # TOTP escribe "estado <> 'revocado'" en su UPDATE de anti-replay
    # (TotpRepository.advance_timestep) — un filtro laxo por la sola
    # subcadena "revocado" contaría también esa escritura.
    revoke_calls = [
        c for c in conn.execute.await_args_list if "SET estado = 'revocado'" in c.args[0]
    ]
    assert len(revoke_calls) == 1
    assert revoke_calls[0].args[1] == existing["id"]


@pytest.mark.asyncio
async def test_enroll_reenroll_active_with_wrong_step_up_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _make_conn()
    existing = _key_row(estado="activo")
    totp_row, _code = _totp_row_and_code(monkeypatch)
    failure_row = {"failed_attempts": 1, "locked_until": None}
    conn.fetchrow.side_effect = [existing, _user_row(), totp_row, failure_row]
    _override_public(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/signing-key/enroll",
            json={"step_up_totp_code": "000000"},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"] == "totp_incorrecto"
    # La clave activa NUNCA se revocó ni se insertó una nueva pendiente.
    revoke_calls = [c for c in conn.execute.await_args_list if "revocado" in c.args[0]]
    assert len(revoke_calls) == 0


# ---------------------------------------------------------------------------
# E17/F4, Incremento 2 — self-revoke (DELETE /me/signing-key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_revoke_requires_step_up_totp_code(client: AsyncClient) -> None:
    """``step_up_totp_code`` es SIEMPRE requerido (a diferencia del re-enroll,
    que solo lo exige sobre ``activo``) — sin él, 422 de validación del
    schema, ni siquiera llega al service."""
    conn = _make_conn()
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/signing-key",
            json={},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 422, resp.text
    assert conn.fetchrow.await_count == 0


@pytest.mark.asyncio
async def test_self_revoke_no_credential_returns_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _make_conn()
    _totp_row, code = _totp_row_and_code(monkeypatch)
    conn.fetchrow.side_effect = [None]  # get_by_user (signing key) -> nada que revocar
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/signing-key",
            json={"step_up_totp_code": code},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "no_signing_key"


@pytest.mark.asyncio
async def test_self_revoke_success_atomic_with_audit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _patch_audit: AsyncMock
) -> None:
    conn = _make_conn()
    active_row = _key_row(estado="activo", certificado_fingerprint="deadbeef")
    revoked_row = _key_row(estado="revocado", certificado_fingerprint="deadbeef")
    totp_row, code = _totp_row_and_code(monkeypatch)
    # get_by_user (signing key), TotpRepository.get_by_user (dentro de
    # verify), revoke_atomic (UPDATE ... RETURNING).
    conn.fetchrow.side_effect = [active_row, totp_row, revoked_row]
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/signing-key",
            json={"step_up_totp_code": code, "motivo": "sospecha de compromiso"},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "revocado"

    # revoke_atomic() escribe vía fetchrow (UPDATE ... RETURNING), NO execute.
    revoke_calls = [
        c for c in conn.fetchrow.await_args_list if "SET estado = 'revocado'" in c.args[0]
    ]
    assert len(revoke_calls) == 1
    assert "revocado_por" in revoke_calls[0].args[0]
    assert revoke_calls[0].args[1] == active_row["id"]
    assert revoke_calls[0].args[2] == USER_ID  # revocado_por = el propio titular
    assert revoke_calls[0].args[3] == "sospecha de compromiso"

    revocada_calls = [
        c for c in _patch_audit.await_args_list if c.kwargs.get("action") == "signing_key.revocada"
    ]
    assert len(revocada_calls) == 1
    kwargs = revocada_calls[0].kwargs
    assert kwargs["actor"] == str(USER_ID)
    assert kwargs["object_ref"] == str(USER_ID)
    assert kwargs["payload"]["via"] == "self"
    assert kwargs["payload"]["motivo"] == "sospecha de compromiso"


@pytest.mark.asyncio
async def test_self_revoke_fails_closed_without_audit_lib(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1 (Baja de auditoría #1, Incremento 2): sin ``orpycamcp_common``,
    ``revoke_self`` se REHÚSA a revocar sin traza — antes el asiento
    ``signing_key.revocada`` se saltaba en silencio y la revocación seguía
    adelante. Ahora falla ANTES de verificar el TOTP (no consume el
    código) y ANTES de tocar la fila (``revoke_atomic`` nunca se invoca)."""
    monkeypatch.setattr("app.services.signing_key_service._AUDIT_AVAILABLE", False)
    conn = _make_conn()
    active_row = _key_row(estado="activo")
    conn.fetchrow.side_effect = [active_row]
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/signing-key",
            json={"step_up_totp_code": "000000", "motivo": "sospecha de compromiso"},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error"] == "auditoria_no_disponible"
    # Solo get_by_user — el TOTP nunca se verifica, revoke_atomic nunca corre.
    assert conn.fetchrow.await_count == 1


@pytest.mark.asyncio
async def test_self_revoke_wrong_totp_never_revokes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _make_conn()
    active_row = _key_row(estado="activo")
    totp_row, _code = _totp_row_and_code(monkeypatch)
    failure_row = {"failed_attempts": 1, "locked_until": None}
    conn.fetchrow.side_effect = [active_row, totp_row, failure_row]
    _override_public(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/signing-key",
            json={"step_up_totp_code": "000000"},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"] == "totp_incorrecto"
    revoke_calls = [c for c in conn.execute.await_args_list if "SET estado = 'revocado'" in c.args[0]]
    assert len(revoke_calls) == 0


# ---------------------------------------------------------------------------
# E17/F4, Incremento 2 — admin-revoke (USUA_PERM_ADMIN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_revoke_requires_permission(client: AsyncClient) -> None:
    conn = _make_conn()
    no_perm_caller = Caller(
        user_id=USER_ID, keycloak_sub="admin-sub", is_root=False, permissions={}
    )
    _override_public(conn, caller=no_perm_caller)
    try:
        resp = await client.post(
            f"/api/v1/auth/admin/users/{OTHER_USER_ID}/signing-key/revoke",
            json={},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 403, resp.text
    assert conn.fetchrow.await_count == 0


@pytest.mark.asyncio
async def test_admin_revoke_success_atomic_with_audit(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    conn = _make_conn()
    admin_caller = Caller(
        user_id=USER_ID,
        keycloak_sub="admin-sub",
        is_root=False,
        permissions={"USUA_PERM_ADMIN": 3},
    )
    target_row = _key_row(estado="activo", user_id=OTHER_USER_ID)
    revoked_row = _key_row(estado="revocado", user_id=OTHER_USER_ID)
    # get_by_user (signing key del TARGET), revoke_atomic (UPDATE ... RETURNING).
    conn.fetchrow.side_effect = [target_row, revoked_row]
    _override_public(conn, caller=admin_caller)
    try:
        resp = await client.post(
            f"/api/v1/auth/admin/users/{OTHER_USER_ID}/signing-key/revoke",
            json={"motivo": "offboarding"},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "revocado"

    revoke_calls = [
        c for c in conn.fetchrow.await_args_list if "SET estado = 'revocado'" in c.args[0]
    ]
    assert len(revoke_calls) == 1
    assert revoke_calls[0].args[2] == USER_ID  # revocado_por = el ADMIN, no el titular

    revocada_calls = [
        c for c in _patch_audit.await_args_list if c.kwargs.get("action") == "signing_key.revocada"
    ]
    assert len(revocada_calls) == 1
    kwargs = revocada_calls[0].kwargs
    assert kwargs["actor"] == str(USER_ID)  # el admin, NO el titular
    assert kwargs["object_ref"] == str(OTHER_USER_ID)  # el titular revocado
    assert kwargs["payload"]["via"] == "admin"
    assert kwargs["payload"]["motivo"] == "offboarding"


@pytest.mark.asyncio
async def test_admin_revoke_fails_closed_without_audit_lib(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1 (Baja de auditoría #1, Incremento 2) — mismo fail-closed que
    ``revoke_self``, camino admin: sin ``orpycamcp_common``, la revocación
    se rehúsa ANTES de tocar la fila."""
    monkeypatch.setattr("app.services.signing_key_service._AUDIT_AVAILABLE", False)
    conn = _make_conn()
    admin_caller = Caller(
        user_id=USER_ID,
        keycloak_sub="admin-sub",
        is_root=False,
        permissions={"USUA_PERM_ADMIN": 3},
    )
    target_row = _key_row(estado="activo", user_id=OTHER_USER_ID)
    conn.fetchrow.side_effect = [target_row]
    _override_public(conn, caller=admin_caller)
    try:
        resp = await client.post(
            f"/api/v1/auth/admin/users/{OTHER_USER_ID}/signing-key/revoke",
            json={"motivo": "offboarding"},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error"] == "auditoria_no_disponible"
    assert conn.fetchrow.await_count == 1  # solo get_by_user; revoke_atomic nunca corre


@pytest.mark.asyncio
async def test_admin_revoke_no_credential_returns_404(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    conn = _make_conn()
    admin_caller = Caller(
        user_id=USER_ID,
        keycloak_sub="admin-sub",
        is_root=False,
        permissions={"USUA_PERM_ADMIN": 3},
    )
    conn.fetchrow.side_effect = [None]
    _override_public(conn, caller=admin_caller)
    try:
        resp = await client.post(
            f"/api/v1/auth/admin/users/{OTHER_USER_ID}/signing-key/revoke",
            json={},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# E17/F4, Incremento 2 — GET /internal/signing-key/revocation-status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revocation_status_vigente(client: AsyncClient) -> None:
    conn = _make_conn()
    conn.fetchrow.side_effect = [_key_row(estado="activo", certificado_fingerprint="deadbeef")]
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/signing-key/revocation-status",
            params={"certificado_fingerprint": "deadbeef"},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "X-Tenant-Slug": "demo"},
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "vigente"
    assert body["revocado_at"] is None
    assert "cert_pem" not in resp.text
    assert "private_key" not in resp.text.lower()


@pytest.mark.asyncio
async def test_revocation_status_revocado(client: AsyncClient) -> None:
    conn = _make_conn()
    revoked_at = datetime.now(UTC)
    conn.fetchrow.side_effect = [
        _key_row(
            estado="revocado", certificado_fingerprint="deadbeef", revocado_at=revoked_at
        )
    ]
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/signing-key/revocation-status",
            params={"certificado_fingerprint": "deadbeef"},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "X-Tenant-Slug": "demo"},
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "revocado"
    assert body["revocado_at"] is not None


@pytest.mark.asyncio
async def test_revocation_status_unknown_fingerprint_returns_desconocido(
    client: AsyncClient,
) -> None:
    """Fail-closed: huella no encontrada (otro tenant, forjada, o titular
    borrado por CASCADE) -> ``desconocido``, NUNCA ``vigente``."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [None]
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/signing-key/revocation-status",
            params={"certificado_fingerprint": "no-existe"},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "X-Tenant-Slug": "otro-tenant"},
        )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "desconocido"
    # Aislamiento por tenant: el search_path se fija al schema DEL HEADER
    # (el mecanismo real de aislamiento — la huella de un tenant nunca
    # aparece en la consulta de otro, ver migración 012 + repository).
    search_path_calls = [
        c for c in conn.execute.await_args_list if "search_path" in c.args[0].lower()
    ]
    assert any("tenant_otro_tenant" in c.args[0] for c in search_path_calls)


@pytest.mark.asyncio
async def test_revocation_status_requires_internal_token(client: AsyncClient) -> None:
    conn = _make_conn()
    _override_internal(conn)
    try:
        resp = await client.get(
            "/internal/signing-key/revocation-status",
            params={"certificado_fingerprint": "deadbeef"},
            headers={"X-Tenant-Slug": "demo"},
        )
    finally:
        _clear()
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# E17/F4, Incremento 2 — sign-digest devuelve ca_cert_pem/ca_fingerprint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_digest_includes_ca_cert_pem_when_anchored(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    sub_ca: tuple[rsa.RSAPrivateKey, x509.Certificate],
) -> None:
    """Una clave ``activo`` anclada (Incremento 2) hace que ``sign-digest``
    devuelva ``ca_cert_pem``/``ca_fingerprint`` de la sub-CA MONTADA — releída
    del filesystem, no de la fila (que solo guarda la huella)."""
    ca_key, ca_cert = sub_ca
    _mock_validate_token(monkeypatch, _claims())
    conn = _make_conn()
    private_key = generate_keypair()
    cert = _issue_test_cert(private_key, ca_key=ca_key, ca_cert=ca_cert)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    user_ca_fingerprint = ca_cert.fingerprint(hashes.SHA256()).hex()
    active_key_row = _key_row(
        private_key=private_key,
        estado="activo",
        cert_pem=cert_pem,
        user_ca_fingerprint=user_ca_fingerprint,
    )

    totp_row, code = _totp_row_and_code(monkeypatch)
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
            headers={"X-Internal-Token": INTERNAL_TOKEN, "Authorization": "Bearer fake.jwt.token"},
        )
    finally:
        _clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ca_fingerprint"] == user_ca_fingerprint
    assert "BEGIN CERTIFICATE" in body["ca_cert_pem"]
    assert x509.load_pem_x509_certificate(
        body["ca_cert_pem"].encode()
    ).fingerprint(hashes.SHA256()).hex() == user_ca_fingerprint


@pytest.mark.asyncio
async def test_sign_digest_omits_ca_cert_pem_for_grandfathered_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una clave ``activo`` SIN ``user_ca_fingerprint`` (anclada antes de
    Incremento 2, o sin sub-CA configurada al firmar) deja
    ``ca_cert_pem``/``ca_fingerprint`` en ``None`` — retrocompat exacta con
    Incremento 1, nunca falla la firma por esto."""
    _mock_validate_token(monkeypatch, _claims())
    conn = _make_conn()
    private_key = generate_keypair()
    cert = _issue_test_cert(private_key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    active_key_row = _key_row(
        private_key=private_key, estado="activo", cert_pem=cert_pem, user_ca_fingerprint=None
    )

    totp_row, code = _totp_row_and_code(monkeypatch)
    conn.fetchrow.side_effect = [_user_row(), active_key_row, totp_row]
    conn.execute.side_effect = lambda *a, **kw: "UPDATE 1"

    digest = hashlib.sha256(b"x").digest()
    _override_internal(conn)
    try:
        resp = await client.post(
            "/internal/signing-key/sign-digest",
            json={"digest_b64": b64encode(digest).decode(), "totp_code": code},
            headers={"X-Internal-Token": INTERNAL_TOKEN, "Authorization": "Bearer fake.jwt.token"},
        )
    finally:
        _clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ca_cert_pem"] is None
    assert body["ca_fingerprint"] is None
