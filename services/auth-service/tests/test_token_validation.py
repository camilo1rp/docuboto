# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Validación de JWT contra el JWKS de Keycloak (migración python-jose → PyJWT).

Firma tokens reales con una clave RSA efímera y expone su clave pública como
JWKS para ejercitar la ruta completa de ``validate_token``: selección de clave
por ``kid``, verificación de firma RS256 y validación del emisor (``iss``).
"""

import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWTError
from jwt.algorithms import RSAAlgorithm

from app.core import security
from tests.conftest import build_jwt_claims

_KID = "test-key-1"


def _keypair_and_jwks() -> tuple[rsa.RSAPrivateKey, dict]:
    """Clave RSA efímera + su JWKS público (con el ``kid`` esperado)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    pub_jwk["kid"] = _KID
    pub_jwk["alg"] = "RS256"
    return key, {"keys": [pub_jwk]}


def _patch_jwks(monkeypatch: pytest.MonkeyPatch, jwks: dict) -> None:
    async def _fake_jwks() -> dict:
        return jwks

    monkeypatch.setattr(security, "_get_jwks", _fake_jwks)


@pytest.mark.asyncio
async def test_validate_token_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    key, jwks = _keypair_and_jwks()
    claims = build_jwt_claims(username="operador", tenant_slug="demo", roles=["operator"])
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": _KID})
    _patch_jwks(monkeypatch, jwks)

    result = await security.validate_token(token)

    assert result["username"] == "operador"
    assert result["tenant_slug"] == "demo"
    assert result["roles"] == ["operator"]


@pytest.mark.asyncio
async def test_validate_token_rejects_bad_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    key, jwks = _keypair_and_jwks()
    claims = build_jwt_claims()
    claims["iss"] = "https://evil.example/realms/otro"  # firma válida, emisor ajeno
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": _KID})
    _patch_jwks(monkeypatch, jwks)

    with pytest.raises(PyJWTError):
        await security.validate_token(token)


@pytest.mark.asyncio
async def test_validate_token_rejects_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    key, jwks = _keypair_and_jwks()
    claims = build_jwt_claims(expired=True)
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": _KID})
    _patch_jwks(monkeypatch, jwks)

    with pytest.raises(PyJWTError):
        await security.validate_token(token)


@pytest.mark.asyncio
async def test_validate_token_rejects_unknown_kid(monkeypatch: pytest.MonkeyPatch) -> None:
    key, jwks = _keypair_and_jwks()
    claims = build_jwt_claims()
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "no-such-kid"})
    _patch_jwks(monkeypatch, jwks)

    with pytest.raises(PyJWTError):
        await security.validate_token(token)


# ---------------------------------------------------------------------------
# AREA-02-04: casos negativos de algoritmo y firma, typ y azp
# ---------------------------------------------------------------------------


def _token(key: object, claims: dict, *, alg: str = "RS256") -> str:
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": _KID})


@pytest.mark.asyncio
async def test_validate_token_rejects_alg_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    token = jwt.encode(build_jwt_claims(), key=None, algorithm="none", headers={"kid": _KID})

    with pytest.raises(PyJWTError):
        await security.validate_token(token)


@pytest.mark.asyncio
async def test_validate_token_rejects_hs256_with_public_key_as_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confusión de algoritmo clásica: HS256 firmado con el PEM público."""
    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    from cryptography.hazmat.primitives import serialization

    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    forged = _hs256_with_pem(pem)

    with pytest.raises(PyJWTError):
        await security.validate_token(forged)


def _hs256_with_pem(pem: bytes) -> str:
    """PyJWT se niega a firmar HS256 con un PEM; se arma a mano (lo que haría un atacante)."""
    import base64
    import hashlib
    import hmac

    def b64(b: bytes) -> bytes:
        return base64.urlsafe_b64encode(b).rstrip(b"=")

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": _KID}).encode())
    body = b64(json.dumps(build_jwt_claims()).encode())
    sig = b64(hmac.new(pem, head + b"." + body, hashlib.sha256).digest())
    return (head + b"." + body + b"." + sig).decode()


@pytest.mark.asyncio
async def test_validate_token_rejects_tampered_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    token = _token(key, build_jwt_claims())
    cabeza, cuerpo, firma = token.split(".")
    firma = ("A" if firma[0] != "A" else "B") + firma[1:]

    with pytest.raises(PyJWTError):
        await security.validate_token(".".join([cabeza, cuerpo, firma]))


@pytest.mark.asyncio
async def test_validate_token_rejects_id_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un ID token (typ=ID) con firma, iss y sub idénticos no es un access token."""
    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    claims = {**build_jwt_claims(), "typ": "ID"}

    with pytest.raises(PyJWTError):
        await security.validate_token(_token(key, claims))


@pytest.mark.asyncio
async def test_validate_token_rejects_unknown_azp(monkeypatch: pytest.MonkeyPatch) -> None:
    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    claims = {**build_jwt_claims(), "azp": "otro-cliente"}

    with pytest.raises(PyJWTError):
        await security.validate_token(_token(key, claims))


@pytest.mark.asyncio
@pytest.mark.parametrize("faltante", ["exp", "iat", "sub", "iss"])
async def test_validate_token_requires_core_claims(
    monkeypatch: pytest.MonkeyPatch, faltante: str
) -> None:
    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    claims = build_jwt_claims()
    del claims[faltante]

    with pytest.raises(PyJWTError):
        await security.validate_token(_token(key, claims))


@pytest.mark.asyncio
@pytest.mark.parametrize("azp", ["orpycamcp-frontend", "api-gateway"])
async def test_validate_token_accepts_declared_clients(
    monkeypatch: pytest.MonkeyPatch, azp: str
) -> None:
    """Control positivo (anti-vacuidad): typ=Bearer y azp declarado se aceptan."""
    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    claims = {**build_jwt_claims(), "typ": "Bearer", "azp": azp}

    result = await security.validate_token(_token(key, claims))

    assert result["username"] == "operador"


@pytest.mark.asyncio
async def test_empty_allowed_azp_rejects_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: sin clientes declarados, ni el cliente legítimo pasa."""
    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    monkeypatch.setattr(security.settings, "allowed_azp", "")

    with pytest.raises(PyJWTError):
        await security.validate_token(_token(key, build_jwt_claims()))


# ---------------------------------------------------------------------------
# AREA-02-12: /validate emite expires_in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_emits_expires_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from app.services.auth_service import AuthService

    key, jwks = _keypair_and_jwks()
    _patch_jwks(monkeypatch, jwks)
    claims = {**build_jwt_claims(tenant_slug=None), "exp": int(time.time()) + 3}
    service = AuthService()
    try:
        res = await service.validate(_token(key, claims), conn=None)
    finally:
        await service.close()

    assert res.valid is True
    assert res.expires_in is not None and 1 <= res.expires_in <= 3
    assert "expires_in" in res.model_dump()
