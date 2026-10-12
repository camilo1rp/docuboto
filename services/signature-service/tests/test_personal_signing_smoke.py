# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Paso 0 (BLOQUEANTE) del incremento "firma electrónica PERSONAL PKI
por-usuario" (E17/F4): valida que la técnica de "firma remota" — construir
``ds:SignedInfo`` con ``signxml`` usando una clave EFÍMERA (solo para que
``XAdESSigner`` complete el árbol) empatada con el CERTIFICADO REAL del
usuario en ``KeyInfo``, extraer ``C14N-exclusiva(ds:SignedInfo)`` y firmar ESE
digest con la clave privada REAL del usuario (custodiada por auth-service,
nunca en este proceso) — produce una firma XAdES que verifica correctamente
contra el certificado real, exactamente igual que si se hubiera firmado con
la clave real desde el principio.

Por qué esto NO es obvio de antemano: ``ds:SignedInfo`` en XML-DSig nunca
contiene material de la clave privada (solo algoritmos + digests de
referencias — el documento, ``KeyInfo`` y, en XAdES, ``SignedProperties``),
así que en teoría es independiente de qué clave firme después. El riesgo real
es de implementación: ¿``signxml`` ignora el ``cert=`` explícito y deriva
``KeyInfo``/``SigningCertificate`` de la clave privada pasada en ``key=``? Si
lo hiciera, ``KeyInfo`` llevaría el certificado equivocado (el de la clave
efímera, no el del usuario real) y la técnica se caería. Este test comprueba
exactamente eso.

Si este test falla, el incremento se PARA (ver instrucciones del
orquestador) — el fallback sería construir ``SignedInfo`` a mano, fuera de
``signxml``, y NO debe improvisarse sin reportarlo.

--- Hallazgo del Paso 0 (documentado aquí porque condiciona la producción) ---
``XAdESSigner.sign()`` (a diferencia de ``XMLSigner.sign()`` base) fija
``always_add_key_value=True`` POR DEFECTO — con ``key=`` una clave efímera,
esto embebe un ``ds:KeyValue`` con la clave PÚBLICA EFÍMERA junto al
``ds:X509Data`` con el certificado REAL, y ``XAdESVerifier.verify()`` rechaza
esa combinación (``InvalidInput: Both X509Data and KeyValue found and they
represent different public keys``) salvo que se pase
``ignore_ambiguous_key_info=True``. La solución adoptada (más limpia que
ignorar la ambigüedad en cada verificación) es pasar explícitamente
``always_add_key_value=False`` al firmar: como ``cert=`` sí produce un
``cert_chain`` no vacío, ``signxml`` omite ``KeyValue`` por completo y
``KeyInfo`` queda con SOLO el certificado real — ver
``app.providers.local.LocalSigner._sign_personal`` (Incremento de firma
personal), que replica exactamente este parámetro.
"""

from __future__ import annotations

import hashlib
from base64 import b64decode, b64encode
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import CanonicalizationMethod, DigestAlgorithm, SignatureMethod
from signxml.xades import XAdESSigner, XAdESVerifier

from app.providers.local import _declarar_transforms

_SIGN_ALG = SignatureMethod.RSA_SHA256
_DIGEST_ALG = DigestAlgorithm.SHA256
_C14N_ALG = CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0
_DS_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}

_DOC_XML = (
    b'<solicitud xmlns="urn:orpyca:firma-personal">'
    b'<acto id="acto-1">Contenido a firmar por el usuario</acto>'
    b"</solicitud>"
)


def _generate_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """CA de test en memoria — mismo patrón que ``tests/test_ocsp.py``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "orpycamcp-dev-ca-usuarios")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _issue_user_cert(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate, cn: str = "usuario-de-prueba"
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Par RSA REAL (R) + certificado de usuario, emitido por la CA de test."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _extract_signed_info_c14n(signed_root: etree._Element) -> bytes:
    signed_info = signed_root.find(".//ds:Signature/ds:SignedInfo", namespaces=_DS_NS)
    assert signed_info is not None, "signxml no produjo ds:SignedInfo"
    return etree.tostring(signed_info, method="c14n", exclusive=True)


def _embedded_cert_der(signed_root: etree._Element) -> bytes:
    cert_el = signed_root.find(
        ".//ds:Signature/ds:KeyInfo/ds:X509Data/ds:X509Certificate", namespaces=_DS_NS
    )
    assert cert_el is not None and cert_el.text, "KeyInfo sin X509Certificate embebido"
    return b64decode(cert_el.text.strip())


def _sign_with_ephemeral_key_and_real_cert(
    xml_bytes: bytes, *, ephemeral_key_pem: bytes, real_cert_pem: bytes
) -> etree._Element:
    """Paso 3 del contrato: firma con signxml usando la clave EFÍMERA, pero
    con el certificado REAL del usuario en KeyInfo — exactamente la forma en
    que ``app.providers.local`` construirá el ``PreparedDoc`` antes de
    delegar el ``SignatureValue`` real a ``remote_sign``."""
    root = etree.fromstring(xml_bytes)
    xades_signer = XAdESSigner(
        signature_algorithm=_SIGN_ALG,
        digest_algorithm=_DIGEST_ALG,
        c14n_algorithm=_C14N_ALG,
    )
    signed_root = xades_signer.sign(
        root, key=ephemeral_key_pem, cert=real_cert_pem, always_add_key_value=False
    )
    # ADR-034: declara los Transforms que signxml omite ANTES de firmar SignedInfo.
    _declarar_transforms(signed_root)
    return signed_root


def _sign_signed_info_with_real_key(signed_info_c14n: bytes, real_key: rsa.RSAPrivateKey) -> bytes:
    """Paso 4 del contrato: SHA-256 del SignedInfo canonicalizado, firmado con
    la clave privada REAL (``Prehashed`` — el mismo patrón que
    ``app.core.signing_keys.sign_digest`` usará en auth-service)."""
    digest = hashlib.sha256(signed_info_c14n).digest()
    return real_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))


def _swap_signature_value(signed_root: etree._Element, signature_der: bytes) -> etree._Element:
    """Paso 5: reemplaza ds:SignatureValue con la firma REAL, en base64."""
    sig_value_el = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
    assert sig_value_el is not None
    sig_value_el.text = b64encode(signature_der).decode("ascii")
    return signed_root


def _verify(signed_root: etree._Element, expected_cert: x509.Certificate) -> bool:
    """Paso 6: ``XAdESVerifier`` POR DEFECTO contra el certificado REAL (sin
    atajos de canonicalización: así verifica un tercero, ADR-034)."""
    try:
        results = XAdESVerifier().verify(signed_root, x509_cert=expected_cert)
    except Exception:  # noqa: BLE001 — cualquier fallo == inválida (fail-closed, ver local.py)
        return False
    return len(results) > 0


# ---------------------------------------------------------------------------
# El test
# ---------------------------------------------------------------------------


def test_paso0_tecnica_a_firma_remota_valida_contra_certificado_real() -> None:
    ca_key, ca_cert = _generate_ca()
    real_key, real_cert = _issue_user_cert(ca_key, ca_cert)  # R
    ephemeral_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # E

    real_cert_pem = _cert_pem(real_cert)

    # --- Paso 3: firma con E, cert de R en KeyInfo ---
    signed_root = _sign_with_ephemeral_key_and_real_cert(
        _DOC_XML, ephemeral_key_pem=_pem(ephemeral_key), real_cert_pem=real_cert_pem
    )

    # Comprobación crítica del riesgo identificado por el orquestador: KeyInfo
    # debe llevar el certificado de R (real), NO uno derivado de E — si
    # signxml ignorara `cert=` y derivara KeyInfo de la clave privada pasada
    # en `key=`, esta aserción fallaría y la técnica se cae aquí mismo.
    embedded_der = _embedded_cert_der(signed_root)
    assert embedded_der == real_cert.public_bytes(serialization.Encoding.DER), (
        "KeyInfo no lleva el certificado REAL (R) — signxml derivó el "
        "certificado de la clave efímera en vez de usar cert= explícito. "
        "La técnica A NO se sostiene: PARAR el incremento."
    )

    # La firma producida por signxml con la clave EFÍMERA es, a propósito,
    # CRIPTOGRÁFICAMENTE INVÁLIDA contra el cert de R en este punto (todavía
    # no hemos sustituido SignatureValue) — no se afirma nada sobre eso aquí.

    # --- Paso 4: extraer C14N-exclusiva(SignedInfo), firmar con R (Prehashed) ---
    signed_info_c14n = _extract_signed_info_c14n(signed_root)
    real_signature = _sign_signed_info_with_real_key(signed_info_c14n, real_key)

    # --- Paso 5: swap de SignatureValue ---
    signed_root = _swap_signature_value(signed_root, real_signature)

    # SignedInfo no debe haberse tocado por el swap (solo SignatureValue) —
    # sanity check de que el swap fue quirúrgico.
    assert _extract_signed_info_c14n(signed_root) == signed_info_c14n

    # --- Paso 6: verificar contra el cert de R → debe validar ---
    assert _verify(signed_root, real_cert) is True, (
        "La firma con clave efímera + swap de SignatureValue NO valida "
        "contra el certificado real — técnica A NO se sostiene."
    )

    # Serializar y re-parsear (round-trip completo, como llegaría desde BD/HTTP)
    # y volver a verificar — confirma que no hay estado en memoria oculto.
    serialized = etree.tostring(signed_root, xml_declaration=False, encoding="UTF-8")
    reparsed = etree.fromstring(serialized)
    assert _verify(reparsed, real_cert) is True

    # --- Alterar un byte del payload → debe FALLAR ---
    tampered = serialized.replace(b"Contenido a firmar", b"CONTENIDO ALTERADO!")
    assert tampered != serialized, "el reemplazo no tuvo efecto — ajustar el fixture"
    tampered_root = etree.fromstring(tampered)
    assert _verify(tampered_root, real_cert) is False


def test_paso0_signed_info_no_depende_de_la_clave_privada() -> None:
    """Firma el MISMO documento dos veces con DOS claves privadas distintas
    (pero el mismo certificado en KeyInfo) y comprueba que ``ds:SignedInfo``
    es BYTE-IDÉNTICO en ambos casos — confirma que ``SignedInfo`` (digests de
    referencias + algoritmos) nunca incluye nada derivado de la clave privada
    de firma, solo del certificado público y del contenido. Es la propiedad
    estructural de XML-DSig en la que se apoya toda la técnica A."""
    ca_key, ca_cert = _generate_ca()
    real_key, real_cert = _issue_user_cert(ca_key, ca_cert)
    real_cert_pem = _cert_pem(real_cert)

    key_a = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_b = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    root_a = _sign_with_ephemeral_key_and_real_cert(
        _DOC_XML, ephemeral_key_pem=_pem(key_a), real_cert_pem=real_cert_pem
    )
    root_b = _sign_with_ephemeral_key_and_real_cert(
        _DOC_XML, ephemeral_key_pem=_pem(key_b), real_cert_pem=real_cert_pem
    )

    # NOTA sobre dos fuentes de no-determinismo AJENAS a la clave privada, que
    # este test debe neutralizar para no producir un falso negativo:
    #   1. xades:SigningTime (dentro de SignedProperties) usa la hora actual —
    #      si las dos llamadas caen en segundos distintos, el digest de
    #      SignedProperties (y por tanto el SignedInfo completo) difiere.
    #   2. signxml asigna un Id ALEATORIO al elemento KeyInfo en cada llamada
    #      a .sign() (p. ej. "SignXMLCertificate<hex>") — ese Id forma parte
    #      del contenido canonicalizado de KeyInfo, así que su digest difiere
    #      SIEMPRE entre dos firmas, incluso con la MISMA clave. Confirmado
    #      empíricamente: no depende de qué clave privada se use.
    # La comprobación sustantiva ("SignedInfo no incluye nada derivado de la
    # clave privada") solo puede aislarse en la referencia al DOCUMENTO
    # (URI=""), que ninguna de las dos fuentes de ruido anteriores toca.
    refs_a = root_a.findall(".//ds:SignedInfo/ds:Reference", namespaces=_DS_NS)
    refs_b = root_b.findall(".//ds:SignedInfo/ds:Reference", namespaces=_DS_NS)
    doc_digest_a = next(
        r.findtext("ds:DigestValue", namespaces=_DS_NS) for r in refs_a if r.get("URI") == ""
    )
    doc_digest_b = next(
        r.findtext("ds:DigestValue", namespaces=_DS_NS) for r in refs_b if r.get("URI") == ""
    )
    assert doc_digest_a == doc_digest_b, (
        "El digest de la referencia al DOCUMENTO difiere entre dos firmas con "
        "claves distintas pero el mismo certificado — SignedInfo SÍ depende de "
        "algo ligado a la clave privada. Técnica A NO se sostiene."
    )

    # Y el certificado embebido en KeyInfo es, en ambos casos, el REAL (R) —
    # nunca uno derivado de la clave efímera de turno (key_a/key_b).
    assert _embedded_cert_der(root_a) == real_cert.public_bytes(serialization.Encoding.DER)
    assert _embedded_cert_der(root_b) == real_cert.public_bytes(serialization.Encoding.DER)
