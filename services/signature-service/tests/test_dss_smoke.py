# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Humo contra EU DSS (ADR-034): lo que firmamos lo valida un tercero.

Solo corre si hay ``DSS_URL`` (p. ej. ``http://127.0.0.1:18765``); sin él se
omite, así que no es parte del gate de CI. Firma con el código real
(``LocalSigner``), manda el XML a ``/services/rest/validation/validateSignature``
y exige que la CRIPTOGRAFÍA pase: firma íntegra y todos los digests de
referencia y de sellos íntegros. La confianza (cadena hasta una raíz) NO se
exige: las CA de desarrollo no están en ninguna lista de confianza, así que el
veredicto admitido es ``TOTAL_PASSED`` o ``INDETERMINATE`` por falta de cadena.

    docker run --rm --network host -e DSS_URL=http://127.0.0.1:18765 \\
        -v <repo>/services/signature-service:/app:ro <imagen> pytest tests/test_dss_smoke.py
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from base64 import b64encode
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.providers.base import PreparedDoc, SignerRef
from app.providers.local import LocalSigner

DSS_URL = os.getenv("DSS_URL")
pytestmark = pytest.mark.skipif(not DSS_URL, reason="DSS_URL no definido: humo contra EU DSS omitido")

_DOC = (
    b'<indice xmlns="urn:orpyca:indice:test"><a id="1">contenido a firmar</a>'
    b"<b>segundo elemento</b></indice>"
)
_INDICACIONES_ADMITIDAS = {("TOTAL_PASSED", None), ("INDETERMINATE", "NO_CERTIFICATE_CHAIN_FOUND")}


def _cert(cn: str, issuer=None, issuer_key=None, *, ca: bool = False, tsa_eku: bool = False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    b = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer.subject if issuer is not None else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
    )
    if ca:
        b = b.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    if tsa_eku:
        b = b.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    return key, b.sign(issuer_key or key, hashes.SHA256())


def _pem_key(k: rsa.RSAPrivateKey) -> bytes:
    return k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _pem(c: x509.Certificate) -> bytes:
    return c.public_bytes(serialization.Encoding.PEM)


def validar_en_dss(xml: bytes) -> dict:
    """POST a DSS; devuelve el informe completo."""
    cuerpo = json.dumps(
        {
            "signedDocument": {"bytes": b64encode(xml).decode(), "name": "firmado.xml"},
            "originalDocuments": [],
            "policy": None,
            "signatureId": None,
        }
    ).encode()
    req = urllib.request.Request(
        f"{DSS_URL}/services/rest/validation/validateSignature",
        data=cuerpo,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — URL de prueba controlada
        return json.load(r)


def exigir_criptografia_integra(informe: dict) -> tuple[str, str | None]:
    """Falla si DSS reporta un digest o una firma no íntegros; devuelve el veredicto."""
    dd = informe["DiagnosticData"]
    firma = dd["Signature"][0]
    assert firma["BasicSignature"]["SignatureIntact"] is True, "SignatureIntact=false"
    for dm in firma["DigestMatcher"]:
        assert dm["DataIntact"] is True, f"referencia {dm['type']} {dm['uri']!r}: DataIntact=false"
    for ts in dd.get("Timestamp") or []:
        for dm in ts["DigestMatcher"]:
            assert dm["DataIntact"] is True, f"sello {ts['Type']}: {dm['type']} DataIntact=false"
    simple = informe["SimpleReport"]["signatureOrTimestampOrEvidenceRecord"][0]["Signature"]
    return simple["Indication"], simple.get("SubIndication")


@pytest.mark.asyncio
async def test_sello_institucional_xades_t_valida_en_dss(monkeypatch: pytest.MonkeyPatch) -> None:
    tsa_key, tsa_cert = _cert("tsa-dss", tsa_eku=True)
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem(tsa_cert), key_pem=_pem_key(tsa_key)),
    )
    key, cert = _cert("sello-dss")
    doc = await LocalSigner().sign(
        PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"),
        SignerRef(cert_pem=_pem(cert), key_pem=_pem_key(key)),
    )
    assert doc.xades_level == "XAdES-T"
    veredicto = exigir_criptografia_integra(validar_en_dss(doc.signed_xml))
    assert veredicto in _INDICACIONES_ADMITIDAS, veredicto


@pytest.mark.asyncio
async def test_firma_personal_valida_en_dss(monkeypatch: pytest.MonkeyPatch) -> None:
    tsa_key, tsa_cert = _cert("tsa-dss", tsa_eku=True)
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem(tsa_cert), key_pem=_pem_key(tsa_key)),
    )
    ca_key, ca_cert = _cert("ca-dss", ca=True)
    user_key, user_cert = _cert("usuario-dss", ca_cert, ca_key)

    async def _remote_sign(digest: bytes) -> bytes:
        return user_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    doc = await LocalSigner().sign(
        PreparedDoc(xml_bytes=_DOC, objeto_tipo="radicado"),
        SignerRef(
            cert_pem=_pem(user_cert),
            key_pem=b"",
            remote_sign=_remote_sign,
            tipo_firma="personal",
            nivel_conformidad="xades_personal_no_acreditado",
            ca_cert_pem=_pem(ca_cert),
        ),
    )
    veredicto = exigir_criptografia_integra(validar_en_dss(doc.signed_xml))
    assert veredicto in _INDICACIONES_ADMITIDAS, veredicto


@pytest.mark.asyncio
async def test_sello_xades_lta_valida_en_dss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incluye el ARCHIVE_TIMESTAMP: su imprint (EN 319 132-1 §5.5.2) debe ser
    íntegro para DSS, no solo para nuestro verificador."""
    from app.core.config import settings
    from tests.test_sign_xml_xades import (
        _build_crl,
        _ca_cert_pem,
        _generate_ca,
        _issue_seal_signed_by_ca,
        _lt_signer_ref,
    )

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    tsa_key, tsa_cert = _cert("tsa-dss", tsa_eku=True)
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem(tsa_cert), key_pem=_pem_key(tsa_key)),
    )
    ca_key, ca_cert = _generate_ca()
    cert_pem, key_pem, _ = _issue_seal_signed_by_ca(ca_key, ca_cert)
    doc = await LocalSigner().sign(
        PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"),
        _lt_signer_ref(cert_pem, key_pem, _ca_cert_pem(ca_cert), _build_crl(ca_key, ca_cert)),
    )
    assert doc.xades_level == "XAdES-LTA"
    informe = validar_en_dss(doc.signed_xml)
    tipos = {t["Type"] for t in informe["DiagnosticData"]["Timestamp"]}
    assert "ARCHIVE_TIMESTAMP" in tipos
    veredicto = exigir_criptografia_integra(informe)
    assert veredicto in _INDICACIONES_ADMITIDAS, veredicto


@pytest.mark.asyncio
async def test_dss_lta_renovada(monkeypatch: pytest.MonkeyPatch) -> None:
    """B10 §8: la firma LTA con el `ArchiveTimeStamp` RENOVADO (TimeStampValidationData + un
    segundo ATS que cubre al primero) la valida un tercero: todos los sellos íntegros."""
    from app.core.config import settings
    from app.providers.local import renovar_archive_timestamp
    from tests.test_sign_xml_xades import (
        _build_crl,
        _ca_cert_pem,
        _generate_ca,
        _issue_seal_signed_by_ca,
        _lt_signer_ref,
    )

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    tsa_key, tsa_cert = _cert("tsa-dss", tsa_eku=True)
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem(tsa_cert), key_pem=_pem_key(tsa_key)),
    )
    ca_key, ca_cert = _generate_ca()
    cert_pem, key_pem, _ = _issue_seal_signed_by_ca(ca_key, ca_cert)
    doc = await LocalSigner().sign(
        PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"),
        _lt_signer_ref(cert_pem, key_pem, _ca_cert_pem(ca_cert), _build_crl(ca_key, ca_cert)),
    )
    renovada = renovar_archive_timestamp(renovar_archive_timestamp(doc.signed_xml).signed_xml)
    informe = validar_en_dss(renovada.signed_xml)
    tipos = [t["Type"] for t in informe["DiagnosticData"]["Timestamp"]]
    assert tipos.count("ARCHIVE_TIMESTAMP") == 3, tipos
    veredicto = exigir_criptografia_integra(informe)
    assert veredicto in _INDICACIONES_ADMITIDAS, veredicto


async def _lta_renovada_con_ocsp_de_tsa(monkeypatch: pytest.MonkeyPatch, tmp_path):  # noqa: ANN001, ANN202
    """Firma LTA cuyo ATS 0 selló una TSA emitida por una CA con AIA OCSP y SIN punto de distribución de
    CRL; se renueva con la revocación de esa TSA por OCSP (C-6). Devuelve (xml renovado, certificado de la CA de la TSA)."""
    from app.core.config import settings
    from app.providers.local import renovar_archive_timestamp
    from tests import c6_pki as pki
    from tests.test_sign_xml_xades import (
        _build_crl,
        _ca_cert_pem,
        _generate_ca,
        _issue_seal_signed_by_ca,
        _lt_signer_ref,
    )

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    ca_tsa = pki.ca_nueva("ca-tsa-dss")
    tsa_key, tsa_cert = pki.tsa_emitida(ca_tsa, cn="tsa-dss-ocsp", ocsp_url=pki.URL_OCSP)
    pki.declarar_ca(monkeypatch, tmp_path, ca_tsa)
    pki.instalar_tsa(monkeypatch, tsa_key, tsa_cert)
    ca_key, ca_cert = _generate_ca()
    cert_pem, key_pem, _ = _issue_seal_signed_by_ca(ca_key, ca_cert)
    doc = await LocalSigner().sign(
        PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"),
        _lt_signer_ref(cert_pem, key_pem, _ca_cert_pem(ca_cert), _build_crl(ca_key, ca_cert)),
    )
    assert doc.xades_level == "XAdES-LTA"
    pki.servir(
        monkeypatch,
        ocsp=lambda pet: pki.respuesta_ocsp(tsa_cert, ca_tsa, nonce=pki.nonce_de(pet)),
    )
    renovada = renovar_archive_timestamp(doc.signed_xml)
    assert renovada.revocacion_tsa_previa == "ocsp"
    return renovada.signed_xml, ca_tsa[1]


def _ocsp_firmado_por(informe: dict, ca_cert: x509.Certificate) -> list[dict]:
    """Entradas OCSP del informe de DSS con la firma íntegra y firmadas por ``ca_cert``.

    DSS identifica un certificado por ``"C-" + SHA-256(DER)`` en mayúsculas. El OCSP de la TSA anterior
    lo firma la propia CA de la TSA (responderID por clave), y DSS lo toma del documento (``INPUT_DOCUMENT``,
    así reporta 6.5 los datos de validación del `TimeStampValidationData`)."""
    huella = "C-" + hashlib.sha256(ca_cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    return [
        r for r in informe["DiagnosticData"].get("Revocation") or ()
        if r.get("Type") == "OCSP"
        and r["BasicSignature"]["SignatureIntact"] is True
        and r["SigningCertificate"]["Certificate"] == huella
    ]


@pytest.mark.asyncio
async def test_dss_lta_renovada_con_ocsp_de_tsa(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """C-6: la renovación cuya revocación de la TSA anterior es una respuesta OCSP (TSA sin CRL) la valida
    un tercero: firma y los DOS `ArchiveTimeStamp` íntegros, y DSS reconoce el OCSP como dato de validación
    del `TimeStampValidationData`."""
    xml, ca_cert = await _lta_renovada_con_ocsp_de_tsa(monkeypatch, tmp_path)
    informe = validar_en_dss(xml)
    tipos = [t["Type"] for t in informe["DiagnosticData"]["Timestamp"]]
    assert tipos.count("ARCHIVE_TIMESTAMP") == 2, tipos
    veredicto = exigir_criptografia_integra(informe)
    assert veredicto in _INDICACIONES_ADMITIDAS, veredicto
    assert _ocsp_firmado_por(informe, ca_cert), (
        "DSS no reconoce, con la firma íntegra, el OCSP de la TSA anterior firmado por su CA"
    )


@pytest.mark.asyncio
async def test_dss_control_negativo_ocsp_alterado_rompe_el_sello_que_lo_cubre(
    monkeypatch: pytest.MonkeyPatch, tmp_path  # noqa: ANN001
) -> None:
    """Calibra el detector: el mismo XML con el `EncapsulatedOCSPValue` alterado deja un `DataIntact=false`
    en el `ArchiveTimeStamp` que cubre el TSVD. Si DSS lo diera por bueno, el caso positivo no probaría nada."""
    from base64 import b64decode, b64encode

    from lxml import etree

    xml, _ca = await _lta_renovada_con_ocsp_de_tsa(monkeypatch, tmp_path)
    raiz = etree.fromstring(xml)
    ns = {"x": "http://uri.etsi.org/01903/v1.3.2#", "v": "http://uri.etsi.org/01903/v1.4.1#"}
    el = raiz.find(".//v:TimeStampValidationData//x:EncapsulatedOCSPValue", ns)
    assert el is not None
    der = bytearray(b64decode(el.text))
    der[-1] ^= 0xFF  # último byte: la firma de la respuesta
    el.text = b64encode(bytes(der)).decode()
    informe = validar_en_dss(etree.tostring(raiz))
    rotos = [
        (t["Type"], dm["type"])
        for t in informe["DiagnosticData"]["Timestamp"]
        for dm in t["DigestMatcher"]
        if dm["DataIntact"] is False
    ]
    assert rotos, "DSS no detectó la alteración del OCSP cubierto por el ArchiveTimeStamp"


def test_control_negativo_firma_anterior_a_adr034_falla_en_dss() -> None:
    """Calibra el detector: la firma vieja del corpus debe dar HASH_FAILURE;
    si DSS la aceptara, el humo de arriba no probaría nada."""
    from pathlib import Path

    xml = (Path(__file__).parent / "fixtures" / "indice_v4_firma_sin_transforms.xml").read_bytes()
    informe = validar_en_dss(xml)
    simple = informe["SimpleReport"]["signatureOrTimestampOrEvidenceRecord"][0]["Signature"]
    assert (simple["Indication"], simple.get("SubIndication")) == ("TOTAL_FAILED", "HASH_FAILURE")
