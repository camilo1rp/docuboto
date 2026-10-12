# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Vigencia del certificado al firmar y al verificar; canonicalización declarada (B7, ronda 2).

  * ARC-M2 / SEG-B3: un certificado caducado (o aún no vigente) NO firma. El sello
    institucional falla antes de producir nada (503 ``sello_institucional_vencido``);
    la firma personal falla antes de ``remote_sign`` —es decir, sin consumir el TOTP— con
    422 ``certificado_personal_vencido``.
  * ARC-M1: ``/verify`` de una firma XAdES-LTA cuyo certificado caducó DESPUÉS de firmar
    juzga la vigencia a la fecha del ``SignatureTimeStamp`` (con aviso). Sin un
    ``ArchiveTimeStamp`` válido sigue siendo ``certificado_fuera_de_vigencia``.
  * SEG-B2 / ARC-B1: ``XmldsigNoConformeError`` en la ruta personal es un 503 limpio.
  * ARC-B2: el verificador aplica la canonicalización que DECLARAN ``SignatureTimeStamp`` y
    ``ArchiveTimeStamp``, no un exc-c14n fijo.

Mutaciones: quitar ``_exigir_certificado_vigente`` de ``LocalSigner.sign`` (caen M2/B3); en
``verify_xades`` devolver siempre ``certificado_fuera_de_vigencia`` al caducar (cae M1);
volver a ``_c14n_element`` fijo en la verificación del sello (cae B2).
"""

from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from lxml import etree
from signxml import CanonicalizationMethod

from app.core import tsa
from app.core.keys import cert_fingerprint
from app.providers import local
from app.providers.base import PreparedDoc, SignerRef
from app.providers.local import (
    CertificadoPersonalVencidoError,
    LocalSigner,
    SelloInstitucionalVencidoError,
    XmldsigNoConformeError,
    verify_xades,
)
from app.services import personal_signing
from tests.test_xmldsig_conformidad import (  # noqa: F401  (_tsa_local es autouse)
    _DOC,
    _NS_LTA,
    _cert,
    _firmar_lta,
    _firmar_sello,
    _pem_cert,
    _pem_key,
    _tsa_local,
)

_INCLUSIVA = CanonicalizationMethod.CANONICAL_XML_1_0.value
_EXCLUSIVA = CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0.value
_AVISO = "certificado_vencido_vigencia_juzgada_al_sello_de_firma"


def _cert_con_vigencia(cn: str, desde: timedelta, hasta: timedelta):
    """Certificado autofirmado con vigencia relativa a ahora."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(UTC)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora + desde)
        .not_valid_after(ahora + hasta)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _reloj_adelantado(monkeypatch: pytest.MonkeyPatch, dias: int) -> None:
    """`datetime.now()` de `app.providers.local` adelantado: el certificado «caduca después de firmar»."""
    real = datetime

    class _Reloj(real):  # type: ignore[valid-type, misc]
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return real.now(tz) + timedelta(days=dias)

    monkeypatch.setattr(local, "datetime", _Reloj)


# ---------------------------------------------------------------------------
# ARC-M2: el sello institucional caducado no firma
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("desde", "hasta", "fragmento"),
    [
        (timedelta(days=-400), timedelta(days=-30), "caducó"),
        (timedelta(days=30), timedelta(days=400), "aún no es vigente"),
    ],
)
async def test_un_sello_fuera_de_vigencia_no_firma(
    monkeypatch: pytest.MonkeyPatch, desde: timedelta, hasta: timedelta, fragmento: str
) -> None:
    key, cert = _cert_con_vigencia("sello-vencido", desde, hasta)
    tsa_llamada = AsyncMock()
    monkeypatch.setattr(tsa, "build_timestamp_token", tsa_llamada)
    signer = SignerRef(cert_pem=_pem_cert(cert), key_pem=_pem_key(key))
    with pytest.raises(SelloInstitucionalVencidoError, match=fragmento):
        await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="indice"), signer)
    tsa_llamada.assert_not_called()  # nada se selló en el tiempo


@pytest.mark.asyncio
async def test_un_sello_vigente_sigue_firmando() -> None:
    xml, _cert_sello = await _firmar_sello()
    assert b"SignatureValue" in xml


# ---------------------------------------------------------------------------
# SEG-B3: la firma personal con certificado caducado no consume el TOTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firma_personal_con_certificado_caducado_no_llama_a_remote_sign() -> None:
    key, cert = _cert_con_vigencia("persona-vencida", timedelta(days=-400), timedelta(days=-1))
    recibidos: list[bytes] = []

    async def _remote_sign(digest: bytes) -> bytes:
        recibidos.append(digest)
        return key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    signer = SignerRef(
        cert_pem=_pem_cert(cert), key_pem=b"", remote_sign=_remote_sign, tipo_firma="personal",
        nivel_conformidad="xades_personal_no_acreditado",
    )
    with pytest.raises(CertificadoPersonalVencidoError):
        await LocalSigner().sign(PreparedDoc(xml_bytes=_DOC, objeto_tipo="radicado"), signer)
    assert recibidos == [], "el TOTP se consume dentro de remote_sign: no debe llegar a llamarse"


@pytest.mark.asyncio
async def test_sign_personal_xml_responde_422_y_no_toca_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _key, cert = _cert_con_vigencia("persona-vencida", timedelta(days=-400), timedelta(days=-1))
    auth = AsyncMock()
    monkeypatch.setattr(personal_signing, "sign_digest_remote", auth)
    with pytest.raises(HTTPException) as info:
        await personal_signing.sign_personal_xml(
            xml_bytes=_DOC, objeto_tipo="radicado", objeto_id="r-1",
            authorization="Bearer x", totp_code="123456", cert_pem=_pem_cert(cert).decode(),
        )
    assert info.value.status_code == 422
    assert info.value.detail["error"] == "certificado_personal_vencido"
    auth.assert_not_called()  # ni TOTP verificado ni contador anti-replay tocado


# ---------------------------------------------------------------------------
# SEG-B2 / ARC-B1: la autoverificación fallida en la ruta personal es un 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firma_no_conforme_en_la_ruta_personal_es_503_limpio(monkeypatch: pytest.MonkeyPatch) -> None:
    key, cert = _cert("persona-b7")
    async def _firma_remota(*_a: object, **_k: object) -> dict:
        digest = _a[1]
        return {"signature_value_b64": b64encode(key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))).decode()}

    monkeypatch.setattr(personal_signing, "sign_digest_remote", _firma_remota)

    def _rota(*_a: object, **_k: object) -> None:
        raise XmldsigNoConformeError("autoverificacion_xmldsig_fallida: simulada")

    monkeypatch.setattr(local, "_autoverificar", _rota)
    with pytest.raises(HTTPException) as info:
        await personal_signing.sign_personal_xml(
            xml_bytes=_DOC, objeto_tipo="radicado", objeto_id="r-1",
            authorization="Bearer x", totp_code="123456", cert_pem=_pem_cert(cert).decode(),
        )
    assert info.value.status_code == 503
    assert info.value.detail["error"] == "firma_no_conforme_xmldsig"


# ---------------------------------------------------------------------------
# ARC-M1: verificación de un LTA cuyo certificado caducó después de firmar
# ---------------------------------------------------------------------------


def _verificar(doc, **extra):  # noqa: ANN001, ANN003, ANN202
    avisos: list[str] = []
    salida = verify_xades(
        doc.signed_xml,
        doc.certificado_fingerprint,
        doc.extra["tsa_cert_fingerprint"],
        doc.extra["ca_fingerprint"],
        advertencias=avisos,
        **extra,
    )
    return salida, avisos


@pytest.mark.asyncio
async def test_lta_con_certificado_caducado_despues_de_firmar_es_valida_con_aviso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    salida, avisos = _verificar(doc)
    assert salida.valida and _AVISO not in avisos, "vigente: se juzga a ahora, sin aviso"

    _reloj_adelantado(monkeypatch, 900)  # el sello (825 días) ya caducó
    salida, avisos = _verificar(doc)
    assert salida.valida, salida.motivo
    assert salida.xades_level == "XAdES-LTA" and salida.archive_timestamp_present
    assert _AVISO in avisos, "se dice que la vigencia se juzgó a la fecha del sello de firma"


@pytest.mark.asyncio
async def test_sin_archive_timestamp_el_certificado_caducado_sigue_siendo_fuera_de_vigencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un XAdES-T (sello de tiempo sin LTA) no prueba la validez a largo plazo."""
    xml, cert = await _firmar_sello()
    huella = cert.fingerprint(hashes.SHA256()).hex()
    _reloj_adelantado(monkeypatch, 400)  # el sello de prueba dura 365 días
    salida = verify_xades(xml, huella, None)
    assert salida.valida is False and salida.motivo == "certificado_fuera_de_vigencia"


@pytest.mark.asyncio
async def test_lta_con_archive_timestamp_invalido_y_certificado_caducado_es_fuera_de_vigencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    root = etree.fromstring(doc.signed_xml)
    ats = root.find(".//x141:ArchiveTimeStamp/xades:EncapsulatedTimeStamp", _NS_LTA)
    otro = tsa.build_timestamp_token(b"otra cosa")
    ats.text = b64encode(otro.token_der).decode("ascii")
    manipulado = etree.tostring(root)

    _reloj_adelantado(monkeypatch, 900)
    salida = verify_xades(
        manipulado, doc.certificado_fingerprint, doc.extra["tsa_cert_fingerprint"], doc.extra["ca_fingerprint"]
    )
    assert salida.valida is False and salida.motivo == "certificado_fuera_de_vigencia"


@pytest.mark.asyncio
async def test_lta_con_el_documento_alterado_no_se_salva_por_estar_caducado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    alterado = doc.signed_xml.replace(b"contenido a firmar", b"contenido ADULTERADO")
    assert alterado != doc.signed_xml
    _reloj_adelantado(monkeypatch, 900)
    salida = verify_xades(
        alterado, doc.certificado_fingerprint, doc.extra["tsa_cert_fingerprint"], doc.extra["ca_fingerprint"]
    )
    assert salida.valida is False


# ---------------------------------------------------------------------------
# ARC-B2: se aplica la canonicalización que el sello de tiempo DECLARA
# ---------------------------------------------------------------------------


def _con_sello_firma(root: etree._Element, *, imprint: str, declarada: str) -> bytes:
    """Reemplaza el `SignatureTimeStamp` por uno cuyo imprint se calculó con `imprint`
    y que DECLARA `declarada`."""
    sv = root.find(".//ds:Signature/ds:SignatureValue", _NS_LTA)
    token = tsa.build_timestamp_token(local._c14n_con_algoritmo(sv, imprint))
    sello = root.find(".//xades:SignatureTimeStamp", _NS_LTA)
    sello.find("ds:CanonicalizationMethod", _NS_LTA).set("Algorithm", declarada)
    sello.find("xades:EncapsulatedTimeStamp", _NS_LTA).text = b64encode(token.token_der).decode("ascii")
    return etree.tostring(root)


@pytest.mark.asyncio
async def test_el_sello_de_firma_se_verifica_con_la_canonicalizacion_declarada() -> None:
    xml, cert = await _firmar_sello()
    huella = cert.fingerprint(hashes.SHA256()).hex()

    # Declarada y aplicada coinciden (inclusiva): válido. Antes se rechazaba: se recomputaba
    # SIEMPRE en exclusiva, sin leer lo declarado.
    coherente = _con_sello_firma(etree.fromstring(xml), imprint=_INCLUSIVA, declarada=_INCLUSIVA)
    huella_tsa = cert_fingerprint(tsa.load_tsa_seal().cert_pem)
    ok = verify_xades(coherente, huella, huella_tsa)
    assert ok.valida and ok.sello_tiempo_valido is True

    # Declara una y se selló con otra: el sello no coincide con lo que dice aplicar.
    incoherente = _con_sello_firma(etree.fromstring(xml), imprint=_EXCLUSIVA, declarada=_INCLUSIVA)
    mal = verify_xades(incoherente, huella, huella_tsa)
    assert mal.sello_tiempo_valido is False


@pytest.mark.asyncio
async def test_sin_metodo_declarado_el_sello_no_se_da_por_valido() -> None:
    xml, cert = await _firmar_sello()
    huella = cert.fingerprint(hashes.SHA256()).hex()
    root = etree.fromstring(xml)
    sello = root.find(".//xades:SignatureTimeStamp", _NS_LTA)
    sello.remove(sello.find("ds:CanonicalizationMethod", _NS_LTA))
    out = verify_xades(etree.tostring(root), huella, cert_fingerprint(tsa.load_tsa_seal().cert_pem))
    assert out.sello_tiempo_valido is False


@pytest.mark.asyncio
async def test_el_archive_timestamp_se_verifica_con_la_canonicalizacion_declarada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    root = etree.fromstring(doc.signed_xml)
    ats = root.find(".//x141:ArchiveTimeStamp", _NS_LTA)
    token = tsa.build_timestamp_token(local._archive_ts_covered_bytes(root, _INCLUSIVA))
    ats.find("ds:CanonicalizationMethod", _NS_LTA).set("Algorithm", _INCLUSIVA)
    ats.find("xades:EncapsulatedTimeStamp", _NS_LTA).text = b64encode(token.token_der).decode("ascii")
    coherente = etree.tostring(root)

    args = (doc.certificado_fingerprint, doc.extra["tsa_cert_fingerprint"], doc.extra["ca_fingerprint"])
    ok = verify_xades(coherente, *args)
    assert ok.valida and ok.xades_level == "XAdES-LTA", ok.motivo

    # Declara inclusiva pero el sello se construyó en exclusiva: no es LTA.
    ats.find("ds:CanonicalizationMethod", _NS_LTA).set("Algorithm", _EXCLUSIVA)
    mal = verify_xades(etree.tostring(root), *args)
    assert mal.valida is False and (mal.motivo or "").startswith("archive_timestamp_invalido")
