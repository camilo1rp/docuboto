# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 §8 — renovación del `ArchiveTimeStamp` de una firma XAdES-LTA (EN 319 132-1 §5.5.2).

Lo que prueba (con la TSA local de pruebas; el job completo contra Postgres está en
`tests/integration/test_b10_lta_renovacion_sql.py`):

  * la firma renovada lleva `TimeStampValidationData` + un segundo ATS y `verify_xades` valida
    CADA ATS sobre sus propios hermanos previos (nivel LTA, 2 sellos);
  * alterar el ATS 0 —antes o después de renovar— invalida la cadena: verificar solo el último
    sello la daría por buena;
  * una cadena cuya TSA anterior ya había caducado al renovar no vale (hueco en la prueba);
  * el sello renovado puede venir de OTRA TSA siempre que esté anclada (`firma_sello_archivo`).

Mutaciones: que `_archive_ts_covered_bytes` corte en el PRIMER ATS (cae
`test_la_entrada_del_ats_renovado_es_la_de_la_norma` y la del TSVD; la ida y vuelta por sí sola es simétrica y no lo vería); verificar solo el último
ATS en `verify_xades` (cae `test_alterar_el_ats_0_antes_de_renovar_invalida_la_cadena`).
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from datetime import UTC, datetime, timedelta

import pytest
from lxml import etree
from signxml.xades import XAdESVerifier

from app.core import tsa
from app.providers.base import SignerRef
from app.providers.local import renovar_archive_timestamp, verify_xades
from tests.test_xmldsig_conformidad import (  # noqa: F401  (_tsa_local es autouse)
    _NS_LTA,
    _cert,
    _firmar_lta,
    _pem_cert,
    _pem_key,
    _tsa_local,
)

_ATS = ".//x141:ArchiveTimeStamp"


def _verificar(doc, xml: bytes, *, tsas_archivo: tuple[str, ...] = ()):
    return verify_xades(
        xml,
        doc.certificado_fingerprint,
        doc.extra["tsa_cert_fingerprint"],
        doc.extra["ca_fingerprint"],
        tsa_fingerprints_archivo=tsas_archivo,
    )


@pytest.mark.asyncio
async def test_renovar_conserva_y_valida_los_ats_anteriores(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, cert, _ca = await _firmar_lta(monkeypatch)
    assert _verificar(doc, doc.signed_xml).archive_timestamp_count == 1

    r1 = renovar_archive_timestamp(doc.signed_xml)
    assert r1.ordinal == 1
    root = etree.fromstring(r1.signed_xml)
    assert len(root.findall(_ATS, _NS_LTA)) == 2
    assert root.find(".//x141:TimeStampValidationData", _NS_LTA) is not None
    # El verificador XAdES de referencia tolera la estructura renovada.
    XAdESVerifier().verify(root, x509_cert=cert)

    salida = _verificar(doc, r1.signed_xml)
    assert salida.valida and salida.xades_level == "XAdES-LTA", salida.motivo
    assert salida.archive_timestamp_count == 2

    # Y se puede seguir renovando: la cadena crece (3 sellos).
    r2 = renovar_archive_timestamp(r1.signed_xml)
    assert r2.ordinal == 2
    salida2 = _verificar(doc, r2.signed_xml)
    assert salida2.valida and salida2.archive_timestamp_count == 3, salida2.motivo


def _cambiar_token(xml: bytes, indice: int) -> bytes:
    """Sustituye el token del ATS `indice` por otro token válido de OTRO contenido."""
    root = etree.fromstring(xml)
    enc = root.findall(_ATS + "/xades:EncapsulatedTimeStamp", _NS_LTA)[indice]
    enc.text = b64encode(tsa.build_timestamp_token(b"otro contenido").token_der).decode()
    return etree.tostring(root)


@pytest.mark.asyncio
async def test_alterar_el_ats_0_antes_de_renovar_invalida_la_cadena(monkeypatch: pytest.MonkeyPatch) -> None:
    """El ATS 1 sella sobre un ATS 0 corrupto: él mismo valida, pero el 0 no. Verificar solo el
    último sello daría esta firma por buena."""
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    corrupto = _cambiar_token(doc.signed_xml, 0)
    renovada = renovar_archive_timestamp(corrupto)

    salida = _verificar(doc, renovada.signed_xml)
    assert not salida.valida
    assert "archive_timestamp_invalido[0]" in (salida.motivo or "")


@pytest.mark.asyncio
async def test_alterar_el_ats_0_despues_de_renovar_invalida_el_0_y_el_1(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    renovada = renovar_archive_timestamp(doc.signed_xml)
    salida = _verificar(doc, _cambiar_token(renovada.signed_xml, 0))
    assert not salida.valida and "archive_timestamp_invalido" in (salida.motivo or "")


@pytest.mark.asyncio
async def test_alterar_el_tsvd_invalida_el_ats_renovado(monkeypatch: pytest.MonkeyPatch) -> None:
    """El sello nuevo cubre el `TimeStampValidationData`: cambiarlo lo rompe."""
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    renovada = renovar_archive_timestamp(doc.signed_xml)
    root = etree.fromstring(renovada.signed_xml)
    cert_el = root.find(".//x141:TimeStampValidationData//xades:EncapsulatedX509Certificate", _NS_LTA)
    cert_el.text = b64encode(b64decode(cert_el.text) + b"\x00").decode()
    salida = _verificar(doc, etree.tostring(root))
    assert not salida.valida and "archive_timestamp_invalido[1]" in (salida.motivo or "")


@pytest.mark.asyncio
async def test_una_tsa_distinta_exige_estar_anclada(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    otra_key, otra_cert = _cert("tsa-nueva", tsa_eku=True)
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem_cert(otra_cert), key_pem=_pem_key(otra_key), key_password=None),
    )
    renovada = renovar_archive_timestamp(doc.signed_xml)
    huella_nueva = renovada.token.tsa_cert_fingerprint
    assert huella_nueva != doc.extra["tsa_cert_fingerprint"]

    sin_ancla = _verificar(doc, renovada.signed_xml)
    assert not sin_ancla.valida and "tsa_no_anclada" in (sin_ancla.motivo or "")
    con_ancla = _verificar(doc, renovada.signed_xml, tsas_archivo=(huella_nueva,))
    assert con_ancla.valida and con_ancla.archive_timestamp_count == 2, con_ancla.motivo


@pytest.mark.asyncio
async def test_cadena_rota_si_la_tsa_anterior_caduco_antes_de_renovar(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    renovada = renovar_archive_timestamp(doc.signed_xml)
    real = tsa.verify_timestamp_token
    llamadas = {"n": 0}

    def _con_tsa_caducada_en_el_0(*a, **kw):
        res = real(*a, **kw)
        llamadas["n"] += 1
        # En la cadena el ATS 0 es el 2.º token verificado (tras el SignatureTimeStamp).
        if llamadas["n"] == 2 and res.valido:
            res.tsa_cert_not_after = datetime.now(UTC) - timedelta(days=1)
        return res

    monkeypatch.setattr(tsa, "verify_timestamp_token", _con_tsa_caducada_en_el_0)
    salida = _verificar(doc, renovada.signed_xml)
    assert not salida.valida and "archive_timestamp_cadena_rota[1]" in (salida.motivo or "")


@pytest.mark.asyncio
async def test_la_entrada_del_ats_renovado_es_la_de_la_norma(monkeypatch: pytest.MonkeyPatch) -> None:
    """EN 319 132-1 §5.5.2 escrita APARTE del código: el ATS 1 sella lo mismo que el 0 MÁS el ATS 0
    y el `TimeStampValidationData`. Sin esta prueba, un cálculo que recortara las «previas» en el
    primer ATS sería simétrico entre construir y verificar y pasaría sin que nadie lo notara."""
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    renovada = renovar_archive_timestamp(doc.signed_xml)
    root = etree.fromstring(renovada.signed_xml)
    ats1 = root.findall(_ATS, _NS_LTA)[1]
    entrada = _entrada_hasta(root, ats1)
    token = b64decode(ats1.find("xades:EncapsulatedTimeStamp", _NS_LTA).text)
    assert tsa.verify_timestamp_token(
        token, entrada, expected_tsa_fingerprint=doc.extra["tsa_cert_fingerprint"]
    ).valido
    # y la lista de previas incluye, además de las tres propiedades LT, el ATS 0 y el TSVD
    nombres = [etree.QName(h).localname for h in root.find(".//xades:UnsignedSignatureProperties", _NS_LTA)]
    assert nombres[-3:] == ["ArchiveTimeStamp", "TimeStampValidationData", "ArchiveTimeStamp"]


def _entrada_hasta(root: etree._Element, objetivo: etree._Element) -> bytes:
    import copy

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
    for hijo in root.find(".//xades:UnsignedSignatureProperties", _NS_LTA):
        if hijo is objetivo:
            break
        partes.append(etree.tostring(hijo, method="c14n", exclusive=True))
    return b"".join(partes)


@pytest.mark.asyncio
async def test_no_se_renueva_lo_que_no_es_lta() -> None:
    from tests.test_xmldsig_conformidad import _firmar_sello

    xml, _cert_sello = await _firmar_sello()
    with pytest.raises(ValueError, match="sin_archive_timestamp"):
        renovar_archive_timestamp(xml)
