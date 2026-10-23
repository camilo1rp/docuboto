# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (ADR-042): `/verify` EVALÚA el `TimeStampValidationData` de cada eslabón.

Un `ArchiveTimeStamp` posterior sella lo anterior, así que cualquier alteración POSTERIOR del TSVD la
caza ya la comprobación del imprint (ya cubierta en `test_b10_lta_renovacion.py`). Lo que se prueba aquí
es otra cosa: un TSVD que el sello nuevo cubrió TAL CUAL pero que no sirve —sin revocación, con una CRL de
otra época, con la TSA revocada, sin su certificado, con algoritmos caducados—. Por eso cada caso
negativo se fabrica ANTES de sellar (sustituyendo la evidencia que `renovar_archive_timestamp` recoge) y
el verificador ha de rechazarlo por su contenido, no por la firma del sello.

Mutaciones (aplicar, correr, revertir): no evaluar el TSVD en `verify_xades` (caen casi todas las de
abajo); tomar `certificados_de_token(...)[0]` para el pin (cae `test_pin_por_el_firmante_no_por_el_primero`);
ignorar `ca_not_after` (cae `test_una_ca_que_caduca_antes_que_el_siguiente_sello_rompe_la_cadena`); juzgar
TODOS los algoritmos «hoy» (cae `test_regla_temporal_de_algoritmos_el_ultimo_hoy_el_resto_al_sello_siguiente`).
"""

from __future__ import annotations

import copy
from base64 import b64decode
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from lxml import etree

from app.core import algoritmos, tsa
from app.providers import local
from app.providers.local import renovar_archive_timestamp, verify_xades
from tests import c6_pki as pki
from tests.test_vigencia_del_certificado_y_sellos import _reloj_adelantado
from tests.test_xmldsig_conformidad import (  # noqa: F401  (_tsa_local es autouse)
    _NS_LTA,
    _firmar_lta,
    _tsa_local,
)

_TSVD = ".//x141:TimeStampValidationData"


def _verificar(doc, xml: bytes, anclas: tuple[str, ...] = ()):  # noqa: ANN001, ANN202
    avisos: list[str] = []
    salida = verify_xades(
        xml, doc.certificado_fingerprint, doc.extra["tsa_cert_fingerprint"], doc.extra["ca_fingerprint"],
        advertencias=avisos, tsa_fingerprints_archivo=anclas,
    )
    return salida, avisos


async def _doc_con_tsa_de_ca(monkeypatch, tmp_path, *, ca=None, declarar=True, **tsa_kw):  # noqa: ANN001, ANN003, ANN202
    """Una firma LTA cuyo ATS 0 selló una TSA emitida por una CA (con CRL y OCSP anunciados).
    ``declarar=False``: la CA NO está en ``TSA_CA_PEM_PATHS`` ni en el token; solo la traerá el TSVD."""
    ca = ca or pki.ca_nueva("ca-a")
    k, cert = pki.tsa_emitida(ca, cn="tsa-a", cdp=pki.URL_CRL, ocsp_url=pki.URL_OCSP, **tsa_kw)
    if declarar:
        pki.declarar_ca(monkeypatch, tmp_path, ca)
    pki.instalar_tsa(monkeypatch, k, cert)
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    return ca, cert, doc


def _gen_ats(xml: bytes, i: int) -> datetime:
    root = etree.fromstring(xml)
    enc = root.findall(".//x141:ArchiveTimeStamp/xades:EncapsulatedTimeStamp", _NS_LTA)[i]
    return tsa.gen_time_de_token(b64decode(enc.text))


def _con_revocacion(monkeypatch, cadena, **datos):  # noqa: ANN001, ANN003, ANN202
    """Hace que `renovar_archive_timestamp` recoja ESTA evidencia en vez de pedirla a la red."""
    monkeypatch.setattr(
        tsa, "revocacion_de_tsa", lambda *a, **k: tsa.RevocacionTsa(cadena=tuple(cadena), **datos)
    )


# ---------------------------------------------------------------------------
# Controles positivos: cadena de tres sellos con CRL, luego OCSP, luego TSA autofirmada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cadena_renovada_dos_veces_con_crl_y_con_ocsp_es_valida(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    ca_a, cert_a, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path)
    ca_b = pki.ca_nueva("ca-b")
    kb, cert_b = pki.tsa_emitida(ca_b, cn="tsa-b", ocsp_url=pki.URL_OCSP)  # sin CRL: solo OCSP
    pki.declarar_ca(monkeypatch, tmp_path, ca_a, ca_b)

    # Renovación 1: la TSA A revoca por CRL; el sello nuevo lo da la TSA B.
    pki.servir(monkeypatch, crl=pki.crl_de(ca_a))
    pki.instalar_tsa(monkeypatch, kb, cert_b)
    r1 = renovar_archive_timestamp(doc.signed_xml)
    assert r1.revocacion_tsa_previa == "crl"

    # Renovación 2: la TSA B no tiene CRL, revoca por OCSP; el sello nuevo lo da una TSA autofirmada.
    pki.servir(monkeypatch, ocsp=lambda pet: pki.respuesta_ocsp(cert_b, ca_b, nonce=pki.nonce_de(pet)))
    kc, cert_c = pki.ca_nueva("tsa-c-autofirmada")
    ahora = datetime.now(UTC)
    cert_c = (
        x509.CertificateBuilder().subject_name(cert_c.subject).issuer_name(cert_c.subject)
        .public_key(kc.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1)).not_valid_after(ahora + timedelta(days=365))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(kc, hashes.SHA256())
    )
    pki.instalar_tsa(monkeypatch, kc, cert_c)
    r2 = renovar_archive_timestamp(r1.signed_xml)
    assert r2.revocacion_tsa_previa == "ocsp"

    anclas = (r1.token.tsa_cert_fingerprint, r2.token.tsa_cert_fingerprint)
    salida, avisos = _verificar(doc, r2.signed_xml, anclas)
    assert salida.valida and salida.xades_level == "XAdES-LTA", salida.motivo
    assert salida.archive_timestamp_count == 3
    info = salida.archive_timestamps
    assert [a["ordinal"] for a in info] == [0, 1, 2]
    assert [a["revocacion"] for a in info] == ["crl", "ocsp", None]
    assert [a["tsa_fingerprint"] for a in info][1:] == list(anclas)
    assert all(a["valido"] and a["gen_time"] and a["vence_at"] for a in info)
    assert info[0]["gen_time"] <= info[1]["gen_time"] <= info[2]["gen_time"]
    assert avisos == []


@pytest.mark.asyncio
async def test_tsa_autofirmada_es_valida_con_la_advertencia_declarada(monkeypatch) -> None:  # noqa: ANN001
    """La limitación ya declarada en ADR-034 §5: sin CA no hay revocación que pedir. Control positivo
    de la rama autofirmada (y de que la advertencia no calla)."""
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    r = renovar_archive_timestamp(doc.signed_xml)
    salida, avisos = _verificar(doc, r.signed_xml)
    assert salida.valida, salida.motivo
    assert avisos == ["tsvd_tsa_autofirmada_sin_revocacion[1]"]
    assert salida.archive_timestamps[0]["revocacion"] == "autofirmada"


# ---------------------------------------------------------------------------
# TSVD que el sello cubrió tal cual pero que no sirve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["sin_revocacion", "crl_anterior_al_sello", "crl_del_futuro", "crl_firma_ajena"])
async def test_revocacion_inservible_invalida_la_cadena(monkeypatch, tmp_path, caso: str) -> None:  # noqa: ANN001
    ca, _cert, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path)
    gen0 = _gen_ats(doc.signed_xml, 0)
    ahora = datetime.now(UTC)
    if caso == "sin_revocacion":
        datos = {"fuente": "autofirmada"}  # una CA-emitida no es autofirmada: no hay CRL ni OCSP
    else:
        firmante = pki.ca_nueva("ca-a") if caso == "crl_firma_ajena" else ca
        # (misma clave de nombre que la CA verdadera: el emisor coincide, la firma no)
        if caso == "crl_firma_ajena":
            firmante = (firmante[0], ca[1])
        last = {"crl_anterior_al_sello": gen0 - timedelta(hours=1), "crl_del_futuro": ahora + timedelta(hours=1)}.get(
            caso, ahora
        )
        datos = {"fuente": "crl", "crl_der": pki.crl_de(firmante, last_update=last)}
    _con_revocacion(monkeypatch, [ca[1]], **datos)
    r = renovar_archive_timestamp(doc.signed_xml)

    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert not salida.valida
    assert (salida.motivo or "").startswith("tsvd_sin_revocacion[1]"), salida.motivo


@pytest.mark.asyncio
async def test_tsa_revocada_antes_del_sello_por_crl_invalida_y_despues_solo_avisa(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    ca, cert, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path)
    gen0 = _gen_ats(doc.signed_xml, 0)
    ahora = datetime.now(UTC)

    antes = pki.crl_de(ca, revocados=((cert.serial_number, gen0 - timedelta(hours=1)),))
    _con_revocacion(monkeypatch, [ca[1]], fuente="crl", crl_der=antes)
    r = renovar_archive_timestamp(doc.signed_xml)
    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert not salida.valida and salida.motivo == "tsvd_tsa_revocada[1]"

    despues = pki.crl_de(ca, revocados=((cert.serial_number, ahora + timedelta(seconds=30)),))
    _con_revocacion(monkeypatch, [ca[1]], fuente="crl", crl_der=despues)
    r2 = renovar_archive_timestamp(doc.signed_xml)
    salida2, avisos = _verificar(doc, r2.signed_xml, (r2.token.tsa_cert_fingerprint,))
    assert salida2.valida, salida2.motivo
    assert avisos == ["tsa_revocada_despues_del_sello[1]"]


@pytest.mark.asyncio
async def test_tsa_revocada_por_ocsp_antes_o_despues_del_sello(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    ca, cert, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path)
    gen0 = _gen_ats(doc.signed_xml, 0)
    ahora = datetime.now(UTC)
    for cuando, valida in ((gen0 - timedelta(hours=1), False), (ahora + timedelta(seconds=30), True)):
        der = pki.respuesta_ocsp(cert, ca, estado="revoked", revocado_en=cuando)
        _con_revocacion(monkeypatch, [ca[1]], fuente="ocsp", ocsp_der=der)
        r = renovar_archive_timestamp(doc.signed_xml)
        salida, avisos = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
        assert salida.valida is valida, salida.motivo
        if valida:
            assert avisos == ["tsa_revocada_despues_del_sello[1]"]
        else:
            assert salida.motivo == "tsvd_tsa_revocada[1]"


@pytest.mark.asyncio
async def test_ocsp_fuera_de_la_ventana_entre_sellos_no_vale(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    ca, cert, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path)
    gen0 = _gen_ats(doc.signed_xml, 0)
    vieja = gen0 - timedelta(hours=2)
    der = pki.respuesta_ocsp(cert, ca, produced_at=vieja, this_update=vieja, next_update=vieja + timedelta(days=9))
    _con_revocacion(monkeypatch, [ca[1]], fuente="ocsp", ocsp_der=der)
    r = renovar_archive_timestamp(doc.signed_xml)
    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert not salida.valida and "tsvd_sin_revocacion[1]" in (salida.motivo or "")
    assert "ocsp_anterior_al_sello_previo" in (salida.motivo or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("falta", ["la_tsa", "la_ca"])
async def test_tsvd_sin_los_certificados_necesarios(monkeypatch, tmp_path, falta: str) -> None:  # noqa: ANN001
    ca, cert, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path)
    huella_tsa = cert.fingerprint(hashes.SHA256()).hex()
    huella_ca = ca[1].fingerprint(hashes.SHA256()).hex()
    quitar = huella_tsa if falta == "la_tsa" else huella_ca
    real = local._insert_certificate_values

    def _sin(parent, *certs_der):  # noqa: ANN001, ANN002, ANN202
        return real(
            parent,
            *[d for d in certs_der if x509.load_der_x509_certificate(d).fingerprint(hashes.SHA256()).hex() != quitar],
        )

    monkeypatch.setattr(local, "_insert_certificate_values", _sin)
    _con_revocacion(monkeypatch, [ca[1]], fuente="crl", crl_der=pki.crl_de(ca))
    r = renovar_archive_timestamp(doc.signed_xml)
    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert not salida.valida and (salida.motivo or "").startswith("tsvd_sin_certificado_tsa[1]")


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["ausente", "multiple"])
async def test_tsvd_ausente_o_repetido(monkeypatch, caso: str) -> None:  # noqa: ANN001
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    real = local._insert_archive_timestamp

    def _manipulado(root, usp):  # noqa: ANN001, ANN202
        tsvd = usp.find("x141:TimeStampValidationData", _NS_LTA)
        if caso == "ausente":
            usp.remove(tsvd)
        else:
            copia = copy.deepcopy(tsvd)
            copia.set("Id", "SignXMLTsvdREPETIDO")  # un `xs:ID` repetido rompería el XSD antes que el TSVD
            usp.append(copia)
        return real(root, usp)

    monkeypatch.setattr(local, "_insert_archive_timestamp", _manipulado)
    r = renovar_archive_timestamp(doc.signed_xml)
    salida, _ = _verificar(doc, r.signed_xml)
    assert not salida.valida and salida.motivo == f"tsvd_{caso}[1]"


# ---------------------------------------------------------------------------
# H7 — la CA caduca antes que el siguiente sello
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_una_ca_que_caduca_antes_que_el_siguiente_sello_rompe_la_cadena(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """La hoja (365 d) sigue viva, pero su CA ya caducó: la cadena hasta el ancla ya no existe.
    Mutación: usar solo el `notAfter` de la hoja -> esta prueba cae."""
    ca = pki.ca_nueva("ca-vencida", hasta=datetime.now(UTC) - timedelta(days=1))
    ca, _cert, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path, ca=ca)
    _con_revocacion(monkeypatch, [ca[1]], fuente="crl", crl_der=pki.crl_de(ca))
    r = renovar_archive_timestamp(doc.signed_xml)
    assert r.vence_at < r.tsa_cert_not_after, "el plazo de renovación lo manda la CA, no la hoja"
    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert not salida.valida and salida.motivo == "archive_timestamp_cadena_rota[1]"


@pytest.mark.asyncio
async def test_la_ca_que_solo_trae_el_tsvd_tambien_cuenta(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """La CA no está configurada ni viaja en el token del sello 0: el único sitio donde aparece es el
    `TimeStampValidationData` del siguiente. Su caducidad también rompe la cadena. Mutación: ignorar el
    `ca_not_after` que sale del TSVD -> esta prueba cae (la de arriba la cubre la CA configurada)."""
    ca = pki.ca_nueva("ca-vencida", hasta=datetime.now(UTC) - timedelta(days=1))
    ca, _cert, doc = await _doc_con_tsa_de_ca(monkeypatch, tmp_path, ca=ca, declarar=False)
    _con_revocacion(monkeypatch, [ca[1]], fuente="crl", crl_der=pki.crl_de(ca))
    r = renovar_archive_timestamp(doc.signed_xml)
    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert not salida.valida and salida.motivo == "archive_timestamp_cadena_rota[1]"


@pytest.mark.asyncio
async def test_el_ultimo_sello_con_su_ca_vencida_degrada_a_lt(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    ca = pki.ca_nueva("ca-vencida", hasta=datetime.now(UTC) - timedelta(days=1))
    k, cert = pki.tsa_emitida(ca)
    pki.declarar_ca(monkeypatch, tmp_path, ca)
    pki.instalar_tsa(monkeypatch, k, cert)
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    salida, avisos = _verificar(doc, doc.signed_xml)
    assert salida.valida and salida.lta_vigente is False and salida.xades_level == "XAdES-LT"
    assert "archive_timestamp_caducado" in avisos


# ---------------------------------------------------------------------------
# H6 — el pin sale del certificado que FIRMÓ, no del primero del SET OF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_por_el_firmante_no_por_el_primero(monkeypatch) -> None:  # noqa: ANN001
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    r = renovar_archive_timestamp(doc.signed_xml)
    ca_cualquiera = pki.ca_nueva("ca-delante")[1]
    real = tsa.certificados_de_token
    # Un SET OF no tiene orden: una TSA con cadena puede entregar la CA DELANTE. Se fuerza.
    monkeypatch.setattr(tsa, "certificados_de_token", lambda d: [ca_cualquiera, *real(d)])
    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert salida.valida, salida.motivo
    assert salida.archive_timestamps[1]["tsa_fingerprint"] == r.token.tsa_cert_fingerprint


# ---------------------------------------------------------------------------
# Catálogo de algoritmos y regla temporal
# ---------------------------------------------------------------------------


def test_el_catalogo_admite_por_lista_y_no_por_exclusion() -> None:
    ahora = datetime.now(UTC)
    for bueno in ("sha256", "sha384", "sha512", "rsa", "ecdsa-p256", "ecdsa-p384"):
        assert algoritmos.vigente_en(bueno, ahora)
    for malo in ("sha1", "md5", "rsa-1024", "ec-secp521r1", "dsa", "inventado"):
        assert not algoritmos.vigente_en(malo, ahora)
    assert algoritmos.identificador_de_clave(rsa.generate_private_key(65537, 1024).public_key()) == "rsa-1024"
    assert algoritmos.identificador_de_clave(rsa.generate_private_key(65537, 2048).public_key()) == "rsa"
    assert algoritmos.identificador_de_clave(ec.generate_private_key(ec.SECP256R1()).public_key()) == "ecdsa-p256"
    assert algoritmos.identificador_de_clave(ec.generate_private_key(ec.SECP521R1()).public_key()) == "ec-secp521r1"


def test_vigente_en_respeta_la_fecha_de_caducidad(monkeypatch) -> None:  # noqa: ANN001
    corte = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setitem(algoritmos.ALGORITMOS, "sha256", corte)
    assert algoritmos.vigente_en("sha256", corte - timedelta(days=1))
    assert algoritmos.vigente_en("sha256", corte)
    assert not algoritmos.vigente_en("sha256", corte + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_sha256_caducado_ya_invalida_la_cadena(monkeypatch) -> None:  # noqa: ANN001
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    r = renovar_archive_timestamp(doc.signed_xml)
    assert _verificar(doc, r.signed_xml)[0].valida
    monkeypatch.setitem(algoritmos.ALGORITMOS, "sha256", datetime.now(UTC) - timedelta(days=1))
    salida, _ = _verificar(doc, r.signed_xml)
    assert not salida.valida and (salida.motivo or "").startswith("algoritmo_no_vigente[0]:sha256")


@pytest.mark.asyncio
async def test_regla_temporal_de_algoritmos_el_ultimo_hoy_el_resto_al_sello_siguiente(monkeypatch) -> None:  # noqa: ANN001
    """sha256 caduca dentro de 30 días y el reloj salta 60: el ATS 0 se juzga en el instante del ATS 1
    (vigente, el algoritmo existía), el ATS 1 —el último— HOY (ya no). El motivo nombra el índice 1.
    Mutación: juzgar todos los sellos «hoy» -> el motivo diría `[0]`."""
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    r = renovar_archive_timestamp(doc.signed_xml)
    monkeypatch.setitem(algoritmos.ALGORITMOS, "sha256", datetime.now(UTC) + timedelta(days=30))
    assert _verificar(doc, r.signed_xml)[0].valida, "control: antes de la caducidad vale"
    _reloj_adelantado(monkeypatch, 60)
    salida, _ = _verificar(doc, r.signed_xml)
    assert not salida.valida and (salida.motivo or "").startswith("algoritmo_no_vigente[1]:sha256"), salida.motivo


@pytest.mark.asyncio
async def test_una_tsa_con_clave_rsa_de_1024_no_es_vigente(monkeypatch) -> None:  # noqa: ANN001
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    k = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    ahora = datetime.now(UTC)
    nombre = pki._nombre("tsa-debil")
    cert = (
        x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1)).not_valid_after(ahora + timedelta(days=365))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(k, hashes.SHA256())
    )
    pki.instalar_tsa(monkeypatch, k, cert)
    r = renovar_archive_timestamp(doc.signed_xml)
    salida, _ = _verificar(doc, r.signed_xml, (r.token.tsa_cert_fingerprint,))
    assert not salida.valida and (salida.motivo or "").startswith("algoritmo_no_vigente[1]:rsa-1024")
