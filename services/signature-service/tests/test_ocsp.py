# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cobertura unitaria de ``app.core.ocsp`` (OCSP stapled en XAdES-LT, E06,
RF-FIR-15) — construcción/verificación de la ``BasicOCSPResponse``,
independiente del router/proveedor (ver ``tests/test_sign_xml_xades.py``
para la integración end-to-end con ``app.providers.local``).

Todo el material (CA, sello, CRL, responder OCSP) se genera enteramente en
memoria con ``cryptography`` — nunca toca el filesystem ni
``scripts/gen-dev-ca.sh``/``scripts/gen-dev-ocsp-responder.sh``, mismo
espíritu que los helpers de ``test_sign_xml_xades.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.core import ocsp as ocsp_module

# ---------------------------------------------------------------------------
# Helpers — certificados/CRL efímeros (nunca tocan el filesystem)
# ---------------------------------------------------------------------------


def _generate_ca(cn: str = "orpycamcp-dev-ca"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
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
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _issue_seal_signed_by_ca(ca_key, ca_cert: x509.Certificate, cn: str = "orpycamcp-dev") -> x509.Certificate:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .sign(ca_key, hashes.SHA256())
    )


def _build_crl(ca_key, ca_cert: x509.Certificate, *, revoked_serials: tuple[int, ...] = ()):
    now = datetime.now(UTC)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now + timedelta(days=3650))
    )
    for serial in revoked_serials:
        revoked = x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(now).build()
        builder = builder.add_revoked_certificate(revoked)
    return builder.sign(private_key=ca_key, algorithm=hashes.SHA256())


def _issue_ocsp_responder(
    ca_key,
    ca_cert: x509.Certificate,
    *,
    cn: str = "orpycamcp-dev-ocsp-responder",
    with_eku: bool = True,
    with_nocheck: bool = True,
):
    """Responder OCSP DELEGADO — EKU ``id-kp-OCSPSigning`` +
    ``id-pkix-ocsp-nocheck``, emitido por ``ca_cert`` (RFC 6960 §4.2.2.2).
    ``with_eku=False``/``with_nocheck=False`` producen certificados
    deliberadamente inválidos para las pruebas de rechazo."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=397))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if with_eku:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.OCSP_SIGNING]), critical=False
        )
    if with_nocheck:
        builder = builder.add_extension(x509.OCSPNoCheck(), critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())
    return key, cert


def _foreign_responder(cn: str = "orpycamcp-foreign-responder"):
    """Responder AUTOFIRMADO (no emitido por ninguna CA legítima) — para la
    prueba de cadena responder->CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.OCSP_SIGNING]), critical=False)
        .add_extension(x509.OCSPNoCheck(), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _fingerprint(cert: x509.Certificate) -> str:
    return cert.fingerprint(hashes.SHA256()).hex()


# ---------------------------------------------------------------------------
# 1) build_ocsp_response produce una respuesta parseable
# ---------------------------------------------------------------------------


def test_build_ocsp_response_produces_parseable_response() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    assert resp.cert_status == "good"
    assert resp.next_update is not None
    assert resp.next_update > resp.this_update
    assert resp.responder_cert_fingerprint == _fingerprint(responder_cert)

    from cryptography.x509.ocsp import OCSPResponseStatus, load_der_ocsp_response

    parsed = load_der_ocsp_response(resp.response_der)
    assert parsed.response_status == OCSPResponseStatus.SUCCESSFUL
    assert parsed.serial_number == seal_cert.serial_number


# ---------------------------------------------------------------------------
# 2) verify OK con material coherente
# ---------------------------------------------------------------------------


def test_verify_ocsp_response_ok_with_coherent_material() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=resp.responder_cert_fingerprint,
    )
    assert outcome.valido is True
    assert outcome.cert_status == "good"
    assert outcome.produced_at is not None
    assert outcome.motivo is None


# ---------------------------------------------------------------------------
# 3) Fail-closed: responder NO emitido por la CA
# ---------------------------------------------------------------------------


def test_verify_rejects_responder_not_issued_by_ca() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)
    foreign_key, foreign_responder_cert = _foreign_responder()

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, foreign_responder_cert, foreign_key, crl=crl)

    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=resp.responder_cert_fingerprint,
    )
    assert outcome.valido is False
    assert outcome.motivo is not None
    assert outcome.motivo.startswith("ocsp_responder_no_emitido_por_ca")


# ---------------------------------------------------------------------------
# 4) Fail-closed: responder sin EKU OCSPSigning
# ---------------------------------------------------------------------------


def test_verify_rejects_responder_without_ocsp_signing_eku() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert, with_eku=False)

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=resp.responder_cert_fingerprint,
    )
    assert outcome.valido is False
    assert outcome.motivo == "ocsp_responder_sin_eku_ocsp_signing"


# ---------------------------------------------------------------------------
# 4b) Fail-closed: responder sin extensión id-pkix-ocsp-nocheck
# ---------------------------------------------------------------------------


def test_verify_rejects_responder_without_nocheck_extension() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert, with_nocheck=False)

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=resp.responder_cert_fingerprint,
    )
    assert outcome.valido is False
    assert outcome.motivo == "ocsp_responder_sin_nocheck"


# ---------------------------------------------------------------------------
# 5) Fail-closed: firma alterada
# ---------------------------------------------------------------------------


def test_verify_rejects_tampered_signature() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert)

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    # Corrompe un byte a mitad del DER (dentro de la firma/tbsResponseData
    # empaquetada) — cualquier alteración debe invalidar la verificación.
    tampered = bytearray(resp.response_der)
    mid = len(tampered) // 2
    tampered[mid] ^= 0xFF
    outcome = ocsp_module.verify_ocsp_response(
        bytes(tampered),
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=resp.responder_cert_fingerprint,
    )
    assert outcome.valido is False
    assert outcome.motivo is not None


# ---------------------------------------------------------------------------
# 6) Fail-closed: CertID de otro serial (respuesta construida para OTRO sello)
# ---------------------------------------------------------------------------


def test_verify_rejects_certid_for_different_serial() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert, cn="sello-a")
    other_seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert, cn="sello-b")
    crl = _build_crl(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert)

    # Respuesta construida para `other_seal_cert`...
    resp = ocsp_module.build_ocsp_response(other_seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    # ...verificada contra `seal_cert` (otro serial) — debe fallar por CertID.
    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=resp.responder_cert_fingerprint,
    )
    assert outcome.valido is False
    assert outcome.motivo == "ocsp_certid_no_coincide"


# ---------------------------------------------------------------------------
# 7) Fail-closed: certStatus REVOKED (CRL con el serial del sello)
# ---------------------------------------------------------------------------


def test_verify_rejects_revoked_cert_status() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert, revoked_serials=(seal_cert.serial_number,))

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)
    assert resp.cert_status == "revoked"

    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=resp.responder_cert_fingerprint,
    )
    assert outcome.valido is False
    assert outcome.cert_status == "revoked"
    assert outcome.motivo == "certificado_sello_revocado_ocsp"


# ---------------------------------------------------------------------------
# 8) Pinning del responder — None y mismatch fallan-cerrado
# ---------------------------------------------------------------------------


def test_verify_pins_responder_fingerprint_none_fails_closed() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert)

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint=None,
    )
    assert outcome.valido is False
    assert outcome.motivo == "ocsp_responder_fingerprint_no_registrado"


def test_verify_pins_responder_fingerprint_mismatch_fails_closed() -> None:
    ca_key, ca_cert = _generate_ca()
    seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl = _build_crl(ca_key, ca_cert)
    responder_key, responder_cert = _issue_ocsp_responder(ca_key, ca_cert)

    resp = ocsp_module.build_ocsp_response(seal_cert, ca_cert, responder_cert, responder_key, crl=crl)

    outcome = ocsp_module.verify_ocsp_response(
        resp.response_der,
        seal_cert=seal_cert,
        ca_cert=ca_cert,
        expected_responder_fingerprint="0" * 64,
    )
    assert outcome.valido is False
    assert outcome.motivo == "ocsp_responder_fingerprint_no_coincide"


# ---------------------------------------------------------------------------
# 9) Fail-closed genérico: DER corrupto no lanza, reporta inválido
# ---------------------------------------------------------------------------


def test_verify_malformed_der_fails_closed_without_raising() -> None:
    outcome = ocsp_module.verify_ocsp_response(
        b"not-a-valid-ocsp-response",
        seal_cert=_generate_ca()[1],
        ca_cert=_generate_ca()[1],
        expected_responder_fingerprint="deadbeef",
    )
    assert outcome.valido is False
    assert outcome.motivo is not None


@pytest.mark.parametrize("crl_serials", [(), ("revoked",)])
def test_ocsp_material_required_error_is_a_runtime_error(crl_serials) -> None:
    """Verificación de contrato liviana: `OcspMaterialRequiredError` es un
    `RuntimeError` (análogo a `LtMaterialRequiredError`), consumible por el
    router como fallo de infraestructura (503)."""
    assert issubclass(ocsp_module.OcspMaterialRequiredError, RuntimeError)
