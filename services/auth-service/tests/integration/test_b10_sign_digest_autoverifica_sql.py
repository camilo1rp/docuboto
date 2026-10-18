# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 §7 — no quemar el TOTP con una firma que no sirve, contra Postgres REAL.

Lo que un mock no puede probar: que el consumo del TOTP (UPDATE de `last_timestep`) se
revierte junto con la transacción cuando la firma no verifica, y que NO se toca cuando la
huella del certificado no coincide."""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from app.core.config import settings
from app.core.signing_keys import (
    build_aad,
    encrypt_private_key,
    generate_keypair,
    private_key_to_pem,
)
from app.core.totp import _hotp, current_timestep, encrypt_secret
from app.core.totp import build_aad as build_totp_aad
from app.services.signing_key_service import SigningKeyService
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not RUN_INTEGRATION,
        reason="requires RUN_INTEGRATION=1 and a live Postgres (see CLAUDE.md)",
    ),
]

_KEK = base64.b64encode(b"1" * 32).decode()
_TOTP_SECRET = b"0123456789012345678901"
_SLUG = "itest"


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "signing_key_encryption_key", _KEK)
    monkeypatch.setattr(settings, "totp_encryption_key", _KEK)
    # Sin asientos: el slug de prueba no debe ensuciar el `audit_log` vivo.
    monkeypatch.setattr("app.services.signing_key_service._AUDIT_AVAILABLE", False)
    monkeypatch.setattr("app.services.totp_service._AUDIT_AVAILABLE", False)


async def _sembrar(conn: asyncpg.Connection) -> tuple[uuid.UUID, str, object]:
    """Usuario + TOTP activo + credencial de firma activa. Devuelve (user, huella, clave pública)."""
    user_id = (
        await conn.fetchrow("INSERT INTO auth_users (username) VALUES ($1) RETURNING id", f"u-{uuid.uuid4().hex[:8]}")
    )["id"]
    ct, nonce = encrypt_secret(
        _TOTP_SECRET,
        key=base64.b64decode(_KEK),
        aad=build_totp_aad(tenant_slug=_SLUG, user_id=str(user_id)),
    )
    await conn.execute(
        "INSERT INTO auth_user_totp (user_id, secret_cifrado, secret_nonce, estado, last_timestep, activated_at)"
        " VALUES ($1, $2, $3, 'activo', $4, now())",
        user_id, ct, nonce, current_timestep() - 5,
    )
    key = generate_keypair()
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "itest")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    pk_ct, pk_nonce = encrypt_private_key(
        private_key_to_pem(key),
        key=base64.b64decode(_KEK),
        aad=build_aad(tenant_slug=_SLUG, user_id=str(user_id)),
    )
    await conn.execute(
        "INSERT INTO auth_user_signing_key (user_id, cert_pem, private_key_cifrado, private_key_nonce,"
        " estado, activated_at) VALUES ($1, $2, $3, $4, 'activo', now())",
        user_id, cert_pem, pk_ct, pk_nonce,
    )
    return user_id, cert.fingerprint(hashes.SHA256()).hex(), key.public_key()


async def _last_timestep(conn: asyncpg.Connection, user_id: uuid.UUID) -> int:
    return await conn.fetchval("SELECT last_timestep FROM auth_user_totp WHERE user_id = $1", user_id)


def _digest() -> bytes:
    return hashlib.sha256(b"C14N(SignedInfo) de prueba").digest()


async def test_huella_distinta_409_sin_consumir_totp(tenant_ctx) -> None:
    _, conn = tenant_ctx
    user_id, _fp, _pub = await _sembrar(conn)
    antes = await _last_timestep(conn, user_id)
    code = _hotp(_TOTP_SECRET, current_timestep())

    with pytest.raises(HTTPException) as exc:
        await SigningKeyService(conn).load_and_sign_digest(
            user_id, _SLUG, _digest(), code, cert_fingerprint="0" * 64
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "certificado_cambiado"
    assert await _last_timestep(conn, user_id) == antes


async def test_firma_que_no_verifica_revierte_el_consumo_del_totp(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, conn = tenant_ctx
    user_id, fp, _pub = await _sembrar(conn)
    antes = await _last_timestep(conn, user_id)
    code = _hotp(_TOTP_SECRET, current_timestep())

    monkeypatch.setattr("app.services.signing_key_service.sign_digest", lambda _k, _d: os.urandom(256))
    with pytest.raises(HTTPException) as exc:
        await SigningKeyService(conn).load_and_sign_digest(
            user_id, _SLUG, _digest(), code, cert_fingerprint=fp
        )
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "firma_digest_no_verifica"
    assert await _last_timestep(conn, user_id) == antes

    # El MISMO código vuelve a servir: el factor no se gastó.
    monkeypatch.undo()
    monkeypatch.setattr(settings, "signing_key_encryption_key", _KEK)
    monkeypatch.setattr(settings, "totp_encryption_key", _KEK)
    monkeypatch.setattr("app.services.signing_key_service._AUDIT_AVAILABLE", False)
    monkeypatch.setattr("app.services.totp_service._AUDIT_AVAILABLE", False)
    res = await SigningKeyService(conn).load_and_sign_digest(
        user_id, _SLUG, _digest(), code, cert_fingerprint=fp
    )
    assert res[0]


async def test_firma_valida_consume_el_totp(tenant_ctx) -> None:
    _, conn = tenant_ctx
    user_id, fp, pub = await _sembrar(conn)
    antes = await _last_timestep(conn, user_id)
    code = _hotp(_TOTP_SECRET, current_timestep())

    firma, *_ = await SigningKeyService(conn).load_and_sign_digest(
        user_id, _SLUG, _digest(), code, cert_fingerprint=fp
    )
    pub.verify(firma, _digest(), padding.PKCS1v15(), Prehashed(hashes.SHA256()))
    assert await _last_timestep(conn, user_id) > antes

    # Control positivo: el mismo código ya no vale una segunda vez.
    with pytest.raises(HTTPException) as exc:
        await SigningKeyService(conn).load_and_sign_digest(
            user_id, _SLUG, _digest(), code, cert_fingerprint=fp
        )
    assert exc.value.status_code in (401, 423)
