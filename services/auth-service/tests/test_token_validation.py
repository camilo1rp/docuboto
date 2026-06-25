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
