# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Conformidad XMLDSig de la firma XAdES (ADR-034, tanda B7).

El defecto: ``signxml`` deja sin ``ds:Transforms`` las referencias a
``SignedProperties`` y ``KeyInfo`` pero calcula su digest en exc-c14n; un
verificador conforme (EU DSS) las canonicaliza con C14N inclusiva y da
``HASH_FAILURE``. Aquí se prueba, sin red:

  * lo firmado verifica con ``XAdESVerifier()`` POR DEFECTO y con
    ``default_reference_c14n_method=CANONICAL_XML_1_0`` (lo que hace DSS);
  * un verificador independiente (lxml + cryptography, sin signxml) coincide;
  * el digest que recibe ``remote_sign`` sale del ``SignedInfo`` FINAL;
  * el sello de tiempo declara la canonicalización que aplica;
  * un artefacto anterior al arreglo (control negativo, copia de una firma real
    del corpus) NO verifica;
  * si el parcheo falla, la autoverificación tumba la firma (fail-closed).
"""

from __future__ import annotations

import copy
import hashlib
from base64 import b64decode, b64encode
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from lxml import etree
from signxml import CanonicalizationMethod
from signxml.exceptions import InvalidDigest
from signxml.xades import XAdESSignatureConfiguration, XAdESVerifier

from app.core import tsa
from app.providers import local
from app.providers.base import PreparedDoc, SignerRef
from app.providers.local import LocalSigner, XmldsigNoConformeError, verify_xades

DS = "http://www.w3.org/2000/09/xmldsig#"
_NS = {"ds": DS}
_FIXTURE = Path(__file__).parent / "fixtures" / "indice_v4_firma_sin_transforms.xml"

_DOC = (
    b'<indice xmlns="urn:orpyca:indice:test"><a id="1">contenido a firmar</a>'
    b"<b>segundo elemento</b></indice>"
)


# ---------------------------------------------------------------------------
# Material criptográfico efímero
# ---------------------------------------------------------------------------


def _cert(cn: str, issuer=None, issuer_key=None, *, ca: bool = False, tsa_eku: bool = False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer.subject if issuer is not None else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
    )
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    if tsa_eku:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True
        )
    return key, builder.sign(issuer_key or key, hashes.SHA256())


def _pem_key(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _pem_cert(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(autouse=True)
def _tsa_local(monkeypatch: pytest.MonkeyPatch) -> None:
    key, cert = _cert("tsa-b7", tsa_eku=True)
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem_cert(cert), key_pem=_pem_key(key), key_password=None),
    )


async def _firmar_sello() -> tuple[bytes, x509.Certificate]:
    key, cert = _cert("sello-b7")
    signer = SignerRef(cert_pem=_pem_cert(cert), key_pem=_pem_key(key))
    doc = await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"), signer)
    return doc.signed_xml, cert


async def _firmar_personal() -> tuple[bytes, x509.Certificate, list[bytes]]:
    """Firma personal con ``remote_sign`` falso respaldado por una clave RSA
    real; devuelve también los digests recibidos."""
    ca_key, ca_cert = _cert("ca-b7", ca=True)
    user_key, user_cert = _cert("usuario-b7", ca_cert, ca_key)
    recibidos: list[bytes] = []

    async def _remote_sign(digest: bytes) -> bytes:
        recibidos.append(digest)
        return user_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    signer = SignerRef(
        cert_pem=_pem_cert(user_cert),
        key_pem=b"",
        remote_sign=_remote_sign,
        tipo_firma="personal",
        nivel_conformidad="xades_personal_no_acreditado",
    )
    doc = await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="radicado"), signer)
    return doc.signed_xml, user_cert, recibidos


# ---------------------------------------------------------------------------
# Verificador independiente: lxml + cryptography, SIN signxml
# ---------------------------------------------------------------------------

_EXC = "http://www.w3.org/2001/10/xml-exc-c14n#"
_INC = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
_ENV = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"


def _c14n(el: etree._Element, uri: str) -> bytes:
    assert uri in (_EXC, _INC), f"c14n no soportada en el verificador de prueba: {uri}"
    return etree.tostring(el, method="c14n", exclusive=(uri == _EXC))


def _verificador_independiente(xml: bytes, cert: x509.Certificate) -> None:
    """Aplica XMLDSig §3 a mano. Una referencia SIN ``Transforms`` se
    canonicaliza con C14N 1.0 inclusiva (§4.4.3.2), como hace DSS. Lanza
    ``AssertionError``/``InvalidSignature`` si algo no cuadra."""
    root = etree.fromstring(xml)
    sig = root.find(".//ds:Signature", _NS)
    signed_info = sig.find("ds:SignedInfo", _NS)
    c14n_si = signed_info.find("ds:CanonicalizationMethod", _NS).get("Algorithm")

    referencias = signed_info.findall("ds:Reference", _NS)
    assert len(referencias) == 3  # documento + SignedProperties + KeyInfo
    for ref in referencias:
        uri = ref.get("URI")
        if uri == "":
            objetivo = copy.deepcopy(root)
            for s in objetivo.findall(".//ds:Signature", _NS):
                s.getparent().remove(s)
        else:
            objetivo = root.xpath(f"//*[@Id='{uri[1:]}']")[0]
        algoritmo = _INC  # sin Transforms: lo que dicta XMLDSig
        transforms = ref.find("ds:Transforms", _NS)
        if transforms is not None:
            for t in transforms.findall("ds:Transform", _NS):
                alg = t.get("Algorithm")
                if alg == _ENV:
                    continue  # ya retirada arriba
                algoritmo = alg
        octetos = _c14n(objetivo, algoritmo)
        esperado = b64decode(ref.find("ds:DigestValue", _NS).text)
        assert hashlib.sha256(octetos).digest() == esperado, f"digest distinto en {uri!r}"

    valor = b64decode(sig.find("ds:SignatureValue", _NS).text)
    cert.public_key().verify(valor, _c14n(signed_info, c14n_si), padding.PKCS1v15(), hashes.SHA256())


# ---------------------------------------------------------------------------
# Firma nueva: conforme
# ---------------------------------------------------------------------------


def _config_dss() -> XAdESSignatureConfiguration:
    """Lo que hace DSS con una referencia sin Transforms: C14N 1.0 inclusiva."""
    return replace(
        XAdESSignatureConfiguration(),
        default_reference_c14n_method=CanonicalizationMethod.CANONICAL_XML_1_0,
    )


@pytest.mark.asyncio
async def test_sello_verifica_con_signxml_por_defecto_y_con_c14n_inclusiva() -> None:
    xml, cert = await _firmar_sello()
    root = etree.fromstring(xml)
    XAdESVerifier().verify(root, x509_cert=cert)
    XAdESVerifier().verify(root, x509_cert=cert, expect_config=_config_dss())


@pytest.mark.asyncio
async def test_firma_personal_verifica_con_signxml_por_defecto_y_con_c14n_inclusiva() -> None:
    xml, cert, _ = await _firmar_personal()
    root = etree.fromstring(xml)
    XAdESVerifier().verify(root, x509_cert=cert)
    XAdESVerifier().verify(root, x509_cert=cert, expect_config=_config_dss())


@pytest.mark.asyncio
async def test_verificador_independiente_acepta_sello_y_personal() -> None:
    xml, cert = await _firmar_sello()
    _verificador_independiente(xml, cert)
    xml_p, cert_p, _ = await _firmar_personal()
    _verificador_independiente(xml_p, cert_p)


@pytest.mark.asyncio
async def test_verificador_independiente_rechaza_un_contenido_alterado() -> None:
    """Control del verificador: si no distinguiera, las pruebas de arriba no
    probarían nada."""
    xml, cert = await _firmar_sello()
    with pytest.raises(AssertionError):
        _verificador_independiente(xml.replace(b"contenido a firmar", b"contenido ALTERADO!"), cert)


@pytest.mark.asyncio
async def test_toda_referencia_declara_sus_transforms_exc_c14n() -> None:
    for xml in ((await _firmar_sello())[0], (await _firmar_personal())[0]):
        root = etree.fromstring(xml)
        refs = root.findall(".//ds:SignedInfo/ds:Reference", _NS)
        assert len(refs) == 3
        for ref in refs:
            algoritmos = [t.get("Algorithm") for t in ref.findall("ds:Transforms/ds:Transform", _NS)]
            assert algoritmos and algoritmos[-1] == _EXC, ref.get("URI")
            # XMLDSig: Transforms precede a DigestMethod.
            assert ref[0].tag == f"{{{DS}}}Transforms"


@pytest.mark.asyncio
async def test_el_digest_enviado_a_remote_sign_sale_del_signed_info_final() -> None:
    """Atrapa el error de orden: calcular el digest ANTES de declarar los
    Transforms firma un SignedInfo que ya no existe."""
    xml, _cert_user, recibidos = await _firmar_personal()
    assert len(recibidos) == 1
    root = etree.fromstring(xml)
    signed_info = root.find(".//ds:SignedInfo", _NS)
    final = hashlib.sha256(
        _c14n(signed_info, signed_info.find("ds:CanonicalizationMethod", _NS).get("Algorithm"))
    ).digest()
    assert recibidos[0] == final


@pytest.mark.asyncio
async def test_el_sello_de_tiempo_declara_la_canonicalizacion_que_aplica() -> None:
    key, cert = _cert("sello-b7-tsa")
    signer = SignerRef(cert_pem=_pem_cert(cert), key_pem=_pem_key(key))
    doc = await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"), signer)
    root = etree.fromstring(doc.signed_xml)
    ts = root.find(".//{http://uri.etsi.org/01903/v1.3.2#}SignatureTimeStamp")
    declarada = ts.find("ds:CanonicalizationMethod", _NS).get("Algorithm")
    sig_value = root.find(".//ds:Signature/ds:SignatureValue", _NS)
    token = b64decode(ts.find("{http://uri.etsi.org/01903/v1.3.2#}EncapsulatedTimeStamp").text)
    # El imprint se recalcula con lo DECLARADO, no con lo que el código asuma.
    resultado = tsa.verify_timestamp_token(
        token,
        local._c14n_con_algoritmo(sig_value, declarada),
        expected_tsa_fingerprint=doc.extra["tsa_cert_fingerprint"],
    )
    assert resultado.valido, resultado.motivo


# ---------------------------------------------------------------------------
# Firma vieja: control negativo (corpus anterior a ADR-034)
# ---------------------------------------------------------------------------


def _cert_del_fixture(xml: bytes) -> x509.Certificate:
    der = etree.fromstring(xml).find(".//ds:X509Certificate", _NS).text
    return x509.load_der_x509_certificate(b64decode(der))


def test_fixture_viejo_no_declara_transforms_en_signedproperties_ni_keyinfo() -> None:
    root = etree.fromstring(_FIXTURE.read_bytes())
    sin = [r.get("URI") for r in root.findall(".//ds:SignedInfo/ds:Reference", _NS)
           if r.find("ds:Transforms", _NS) is None]
    assert len(sin) == 2  # SignedProperties y KeyInfo: justo el defecto


def test_firma_vieja_no_verifica_con_signxml_por_defecto() -> None:
    xml = _FIXTURE.read_bytes()
    cert = _cert_del_fixture(xml)
    # Se fija el instante dentro de la vigencia: el motivo del fallo debe ser el digest.
    config = XAdESSignatureConfiguration(verification_time=cert.not_valid_before_utc + timedelta(days=1))
    with pytest.raises(InvalidDigest):
        XAdESVerifier().verify(etree.fromstring(xml), x509_cert=cert, expect_config=config)


def test_firma_vieja_no_verifica_con_el_verificador_independiente() -> None:
    xml = _FIXTURE.read_bytes()
    with pytest.raises(AssertionError, match="digest distinto"):
        _verificador_independiente(xml, _cert_del_fixture(xml))


def test_verify_xades_rechaza_la_firma_vieja_sin_atajo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si alguien reintroduce ``default_reference_c14n_method=EXCLUSIVE`` en
    ``verify_xades``, esta firma pasaría a validar y esta prueba cae."""
    xml = _FIXTURE.read_bytes()
    cert = _cert_del_fixture(xml)
    ahora = cert.not_valid_before_utc + timedelta(days=1)

    class _Reloj(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return ahora

    monkeypatch.setattr(local, "datetime", _Reloj)
    salida = verify_xades(xml, cert.fingerprint(hashes.SHA256()).hex())
    assert salida.valida is False
    assert (salida.motivo or "").startswith("firma_invalida")


@pytest.mark.asyncio
async def test_verify_xades_acepta_la_firma_nueva() -> None:
    xml, cert = await _firmar_sello()
    salida = verify_xades(xml, cert.fingerprint(hashes.SHA256()).hex())
    assert salida.valida is True


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_si_el_parcheo_no_ocurre_la_firma_no_se_emite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "_declarar_transforms", lambda root: 0)
    key, cert = _cert("sello-b7-sin-parche")
    signer = SignerRef(cert_pem=_pem_cert(cert), key_pem=_pem_key(key))
    with pytest.raises(XmldsigNoConformeError):
        await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"), signer)


@pytest.mark.asyncio
async def test_si_el_signed_info_se_firma_antes_de_parchear_la_firma_no_se_emite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orden equivocado: la SignatureValue quedaría sobre un SignedInfo que
    ya no existe. La autoverificación lo detecta."""
    original = local._declarar_transforms
    key, cert = _cert("sello-b7-orden")
    signer = SignerRef(cert_pem=_pem_cert(cert), key_pem=_pem_key(key))

    # Simula el orden roto sobre el mismo código: firma, y luego parchea.
    firmar = local._firmar_signed_info
    llamado: list[str] = []

    async def _firmar_y_luego_parchear(signed_root, s):
        llamado.append("firma")
        res = await firmar(signed_root, s)
        original(signed_root)
        llamado.append("parche")
        return res

    monkeypatch.setattr(local, "_declarar_transforms", lambda root: 0)
    monkeypatch.setattr(local, "_firmar_signed_info", _firmar_y_luego_parchear)
    with pytest.raises(XmldsigNoConformeError):
        await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"), signer)
    assert llamado == ["firma", "parche"]


# ---------------------------------------------------------------------------
# XAdES-LTA: la entrada del ArchiveTimeStamp es la de EN 319 132-1 §5.5.2
# ---------------------------------------------------------------------------

_X = "http://uri.etsi.org/01903/v1.3.2#"
_X141 = "http://uri.etsi.org/01903/v1.4.1#"
_NS_LTA = {**_NS, "xades": _X, "x141": _X141}


async def _firmar_lta(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import settings
    from tests.test_sign_xml_xades import (
        _build_crl,
        _ca_cert_pem,
        _generate_ca,
        _issue_seal_signed_by_ca,
        _lt_signer_ref,
    )

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    ca_key, ca_cert = _generate_ca()
    cert_pem, key_pem, cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    signer = _lt_signer_ref(cert_pem, key_pem, _ca_cert_pem(ca_cert), _build_crl(ca_key, ca_cert))
    doc = await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"), signer)
    assert doc.xades_level == "XAdES-LTA"
    return doc, cert, ca_cert


def _entrada_independiente(root: etree._Element) -> bytes:
    """EN 319 132-1 §5.5.2 escrita aparte del código: octetos de cada
    referencia, SignedInfo, SignatureValue, KeyInfo y propiedades no firmadas
    previas al ArchiveTimeStamp, en orden."""
    sig = root.find(".//ds:Signature", _NS_LTA)
    signed_info = sig.find("ds:SignedInfo", _NS_LTA)
    sin_firma = copy.deepcopy(root)
    sin_firma.remove(sin_firma.find("ds:Signature", _NS_LTA))
    partes = [etree.tostring(sin_firma, method="c14n", exclusive=True)]
    for ref in signed_info.findall("ds:Reference", _NS_LTA)[1:]:
        el = root.xpath(f"//*[@Id='{ref.get('URI')[1:]}']")[0]
        partes.append(etree.tostring(el, method="c14n", exclusive=True))
    for tag in ("SignedInfo", "SignatureValue", "KeyInfo"):
        partes.append(etree.tostring(sig.find(f"ds:{tag}", _NS_LTA), method="c14n", exclusive=True))
    usp = root.find(".//xades:UnsignedSignatureProperties", _NS_LTA)
    for hijo in usp:
        if etree.QName(hijo).localname == "ArchiveTimeStamp":
            break
        partes.append(etree.tostring(hijo, method="c14n", exclusive=True))
    return b"".join(partes)


@pytest.mark.asyncio
async def test_lta_verifica_con_signxml_y_con_el_independiente(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, cert, _ca = await _firmar_lta(monkeypatch)
    root = etree.fromstring(doc.signed_xml)
    XAdESVerifier().verify(root, x509_cert=cert)
    XAdESVerifier().verify(root, x509_cert=cert, expect_config=_config_dss())
    _verificador_independiente(doc.signed_xml, cert)


@pytest.mark.asyncio
async def test_lta_entrada_del_archive_timestamp_coincide_con_la_de_la_norma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    root = etree.fromstring(doc.signed_xml)
    ats = root.find(".//x141:ArchiveTimeStamp/xades:EncapsulatedTimeStamp", _NS_LTA)
    resultado = tsa.verify_timestamp_token(
        b64decode(ats.text),
        _entrada_independiente(root),
        expected_tsa_fingerprint=doc.extra["archive_tsa_cert_fingerprint"],
    )
    assert resultado.valido, resultado.motivo
    # Y verify_xades, que usa el código de producción, concluye lo mismo.
    salida = verify_xades(
        doc.signed_xml,
        doc.certificado_fingerprint,
        doc.extra["tsa_cert_fingerprint"],
        doc.extra["ca_fingerprint"],
    )
    assert salida.valida and salida.xades_level == "XAdES-LTA", salida.motivo


@pytest.mark.asyncio
async def test_lta_con_la_entrada_antigua_del_archive_timestamp_no_se_declara_lta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La composición previa a B7 (SignedInfo||SignatureValue||KeyInfo||
    SignedProperties||sellos||...) sin los octetos de las referencias no es la
    de la norma: un sello construido así NO debe validar como LTA."""
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    root = etree.fromstring(doc.signed_xml)
    sig = root.find(".//ds:Signature", _NS_LTA)
    usp = root.find(".//xades:UnsignedSignatureProperties", _NS_LTA)
    antiguos = [
        sig.find("ds:SignedInfo", _NS_LTA),
        sig.find("ds:SignatureValue", _NS_LTA),
        sig.find("ds:KeyInfo", _NS_LTA),
        root.find(".//xades:SignedProperties", _NS_LTA),
        usp.find("xades:SignatureTimeStamp", _NS_LTA),
        usp.find("xades:CertificateValues", _NS_LTA),
        usp.find("xades:RevocationValues", _NS_LTA),
    ]
    token = tsa.build_timestamp_token(
        b"".join(etree.tostring(e, method="c14n", exclusive=True) for e in antiguos)
    )
    ats = usp.find("x141:ArchiveTimeStamp/xades:EncapsulatedTimeStamp", _NS_LTA)
    ats.text = b64encode(token.token_der).decode("ascii")
    salida = verify_xades(
        etree.tostring(root),
        doc.certificado_fingerprint,
        doc.extra["tsa_cert_fingerprint"],
        doc.extra["ca_fingerprint"],
    )
    assert salida.valida is False
    assert (salida.motivo or "").startswith("archive_timestamp_invalido")

