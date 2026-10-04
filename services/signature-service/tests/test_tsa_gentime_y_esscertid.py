# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-04-08 — el sello de tiempo se cruza con el certificado firmante y con el ESSCertID.

1. El ``genTime`` del ``SignatureTimeStamp`` tiene que caer dentro de la vigencia del
   certificado FIRMANTE: es lo que atestigua que la firma existió mientras ese
   certificado era válido (el ``ArchiveTimeStamp`` queda fuera, se emite después
   a propósito).
2. El atributo firmado ``signing-certificate-v2`` (RFC 5035) nombra el certificado
   de la TSA: si está, su hash debe coincidir; si falta, se rechaza solo con
   ``tsa_esscertid_obligatorio`` (los tokens locales anteriores no lo llevan).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from asn1crypto import algos, cms
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core import tsa as tsa_module
from app.core.config import settings
from app.providers.base import SignerRef
from tests.test_sign_xml_xades import _generate_cert, _generate_tsa_cert

IMPRINT_SRC = b"<ds:SignatureValue>firma de prueba</ds:SignatureValue>"


@pytest.fixture
def token_y_tsa(monkeypatch: pytest.MonkeyPatch):
    cert_pem, key_pem = _generate_tsa_cert()
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=cert_pem, key_pem=key_pem, key_password=None),
    )
    token = tsa_module.build_timestamp_token(IMPRINT_SRC)
    return token, cert_pem, key_pem


def _firmante(*, not_before: datetime, not_after: datetime) -> x509.Certificate:
    pem, _ = _generate_cert("firmante", not_before=not_before, not_after=not_after)
    return x509.load_pem_x509_certificate(pem)


def _verificar(token, signer_cert=None):
    return tsa_module.verify_timestamp_token(
        token.token_der,
        IMPRINT_SRC,
        expected_tsa_fingerprint=token.tsa_cert_fingerprint,
        signer_cert=signer_cert,
    )


def test_token_nuevo_valido_sin_y_con_certificado_firmante_vigente(token_y_tsa) -> None:
    token, _, _ = token_y_tsa
    assert _verificar(token).valido is True
    ahora = datetime.now(UTC)
    vigente = _firmante(not_before=ahora - timedelta(days=1), not_after=ahora + timedelta(days=1))
    assert _verificar(token, vigente).valido is True


def test_gentime_fuera_de_vigencia_del_sello(token_y_tsa) -> None:
    """genTime posterior al ``not_valid_after`` del firmante: no atestigua que la
    firma existiera con el certificado vigente."""
    token, _, _ = token_y_tsa
    ahora = datetime.now(UTC)
    caducado = _firmante(
        not_before=ahora - timedelta(days=10), not_after=ahora - timedelta(days=1)
    )
    r = _verificar(token, caducado)
    assert r.valido is False
    assert r.motivo == "gentime_fuera_de_vigencia_firmante"


def test_gentime_anterior_a_la_vigencia_del_firmante(token_y_tsa) -> None:
    token, _, _ = token_y_tsa
    ahora = datetime.now(UTC)
    futuro = _firmante(not_before=ahora + timedelta(days=1), not_after=ahora + timedelta(days=9))
    r = _verificar(token, futuro)
    assert r.valido is False
    assert r.motivo == "gentime_fuera_de_vigencia_firmante"


def test_token_local_lleva_esscertid_v2_con_el_hash_del_certificado_tsa(token_y_tsa) -> None:
    token, cert_pem, _ = token_y_tsa
    ci = cms.ContentInfo.load(token.token_der)
    attrs = cms.CMSAttributes(ci["content"]["signer_infos"][0]["signed_attrs"])
    ess = [a for a in attrs if a["type"].native == "signing_certificate_v2"]
    assert len(ess) == 1
    cert_der = x509.load_pem_x509_certificate(cert_pem).public_bytes(serialization.Encoding.DER)
    primero = ess[0]["values"][0]["certs"][0]
    assert primero["cert_hash"].native == hashlib.sha256(cert_der).digest()


def test_esscertid_no_coincide(token_y_tsa) -> None:
    """Un token cuyo ESSCertID nombra OTRO certificado se rechaza. Se altera el hash
    dentro del DER sin re-firmar: la comprobación del ESSCertID va antes que la de
    la firma CMS, así que lo que se prueba es el cotejo en sí."""
    token, cert_pem, _ = token_y_tsa
    cert_der = x509.load_pem_x509_certificate(cert_pem).public_bytes(serialization.Encoding.DER)
    bueno = hashlib.sha256(cert_der).digest()
    assert token.token_der.count(bueno) == 1
    alterado = token.token_der.replace(bueno, hashlib.sha256(b"otro certificado").digest())
    r = tsa_module.verify_timestamp_token(
        alterado, IMPRINT_SRC, expected_tsa_fingerprint=token.tsa_cert_fingerprint
    )
    assert r.valido is False
    assert r.motivo == "esscertid_no_coincide"


def _sin_esscertid(token_der: bytes, key_pem: bytes) -> bytes:
    """Re-emite el token SIN el atributo ESSCertID (como los tokens locales de antes)."""
    ci = cms.ContentInfo.load(token_der)
    sd = ci["content"]
    si = sd["signer_infos"][0]
    viejos = cms.CMSAttributes(si["signed_attrs"])
    nuevos = cms.CMSAttributes(
        [a for a in viejos if a["type"].native != "signing_certificate_v2"]
    )
    key = serialization.load_pem_private_key(key_pem, password=None)
    firma = key.sign(nuevos.dump(), padding.PKCS1v15(), hashes.SHA256())
    nuevo_si = cms.SignerInfo(
        {
            "version": si["version"],
            "sid": si["sid"],
            "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
            "signed_attrs": nuevos,
            "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "rsassa_pkcs1v15"}),
            "signature": firma,
        }
    )
    sd["signer_infos"] = cms.SignerInfos([nuevo_si])
    return ci.dump()


def test_token_legado_sin_esscertid_se_acepta_por_defecto(token_y_tsa) -> None:
    token, _, key_pem = token_y_tsa
    legado = _sin_esscertid(token.token_der, key_pem)
    assert settings.tsa_esscertid_obligatorio is False
    r = tsa_module.verify_timestamp_token(
        legado, IMPRINT_SRC, expected_tsa_fingerprint=token.tsa_cert_fingerprint
    )
    assert r.valido is True, r.motivo


def test_token_sin_esscertid_se_rechaza_si_la_politica_lo_exige(
    token_y_tsa, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, _, key_pem = token_y_tsa
    legado = _sin_esscertid(token.token_der, key_pem)
    monkeypatch.setattr(settings, "tsa_esscertid_obligatorio", True)
    r = tsa_module.verify_timestamp_token(
        legado, IMPRINT_SRC, expected_tsa_fingerprint=token.tsa_cert_fingerprint
    )
    assert r.valido is False
    assert r.motivo == "tsa_sin_esscertid"
    # …y el token NUEVO sigue valiendo bajo la política estricta.
    assert _verificar(token).valido is True
