# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 §7 — no quemar el TOTP con una firma que no verifica (lado signature).

El TOTP lo consume auth-service DENTRO de `remote_sign`. Por eso el ensayo de XMLDSig (par y
certificado efímeros) debe correr ANTES: un documento que no firmaría conforme falla con el
factor intacto. Además `sign_personal_xml` manda a auth la huella del certificado que va a
incrustar (auth responde 409 si cambió).

Mutaciones: quitar la llamada a `_ensayar_xmldsig` de `LocalSigner.sign` (cae
`test_ensayo_falla_no_llama_remote_sign`); no enviar `cert_fingerprint` (cae
`test_la_huella_del_certificado_viaja_a_auth`).
"""

from __future__ import annotations

import hashlib
from base64 import b64encode

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from app.providers import local
from app.providers.base import PreparedDoc, SignerRef
from app.providers.local import LocalSigner, XmldsigNoConformeError
from app.services import personal_signing
from tests.test_xmldsig_conformidad import (  # noqa: F401  (_tsa_local es autouse)
    _DOC,
    _cert,
    _pem_cert,
    _tsa_local,
)


def _signer(key, cert, remote) -> SignerRef:
    return SignerRef(
        cert_pem=_pem_cert(cert),
        key_pem=b"",
        remote_sign=remote,
        tipo_firma="personal",
        nivel_conformidad="xades_personal_no_acreditado",
    )


@pytest.mark.asyncio
async def test_ensayo_falla_no_llama_remote_sign(monkeypatch: pytest.MonkeyPatch) -> None:
    key, cert = _cert("persona-ensayo")
    llamadas: list[bytes] = []

    async def _remote(digest: bytes) -> bytes:
        llamadas.append(digest)  # llegar aquí == TOTP gastado
        raise AssertionError("remote_sign no debe llamarse si el ensayo falla")

    original = local._declarar_transforms

    def _corrompe(signed_root):
        n = original(signed_root)
        # Un digest alterado tras firmar es lo que un verificador conforme rechaza.
        d = signed_root.find(".//{http://www.w3.org/2000/09/xmldsig#}DigestValue")
        d.text = b64encode(b"x" * 32).decode()
        return n

    monkeypatch.setattr(local, "_declarar_transforms", _corrompe)
    with pytest.raises(XmldsigNoConformeError):
        await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="radicado"), _signer(key, cert, _remote))
    assert llamadas == []


@pytest.mark.asyncio
async def test_ensayo_ok_sigue_a_remote_sign() -> None:
    key, cert = _cert("persona-ensayo-ok")
    llamadas: list[bytes] = []

    async def _remote(digest: bytes) -> bytes:
        llamadas.append(digest)
        return key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    firmado = await LocalSigner().sign(
        PreparedDoc(xml_bytes=_DOC, objeto_tipo="radicado"), _signer(key, cert, _remote)
    )
    assert len(llamadas) == 1
    assert b"SignatureValue" in firmado.signed_xml


@pytest.mark.asyncio
async def test_la_huella_del_certificado_viaja_a_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    key, cert = _cert("persona-huella")
    recibido: dict = {}

    async def _auth(authorization, digest, totp_code, **kw):
        recibido.update(kw)
        sig = key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))
        return {"signature_value_b64": b64encode(sig).decode()}

    monkeypatch.setattr(personal_signing, "sign_digest_remote", _auth)
    await personal_signing.sign_personal_xml(
        xml_bytes=_DOC, objeto_tipo="radicado", objeto_id="r-1",
        authorization="Bearer x", totp_code="123456", cert_pem=_pem_cert(cert).decode(),
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    assert recibido["cert_fingerprint"] == hashlib.sha256(der).hexdigest()
