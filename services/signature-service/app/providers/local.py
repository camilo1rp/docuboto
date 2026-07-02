# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""LocalSigner — proveedor XAdES-B enveloped local, vía ``signxml`` (E06, Inc.1).

Produce una firma XAdES-B REAL (XML-DSig elevada a XAdES con
``xades:QualifyingProperties``/``SignedProperties``: sello de tiempo del
firmante + referencia al certificado) con el sello institucional del tenant
(``app.core.keys.load_seal`` — nunca la clave de un usuario). El firmante
concreto se atribuye por ``X-User-Id`` + ``audit_log`` (RF-FIR-11), no por el
material de firma: la clave siempre es la del sistema/institución.

Algoritmos (fijos en el Incremento 1 — RT-16):
    - Firma: RSA-SHA256 (``SignatureMethod.RSA_SHA256``); el certificado de
      pruebas usa RSA-2048.
    - Digest: SHA-256.
    - Canonicalización: Exclusiva 1.0 (``http://www.w3.org/2001/10/xml-exc-c14n#``).
    - Construcción: enveloped — ``<ds:Signature>`` se inserta como HIJO de la
      raíz ``<indice>`` (comportamiento por defecto de ``signxml`` cuando no
      hay un placeholder ``<ds:Signature Id="placeholder">`` en el documento).

NO SE RE-CANONICALIZA EL CUERPO antes de firmar: ``prepared.xml_bytes`` llega
ya canonicalizado C14N 2.0 por
``archive-service/app/services/index_builder.build_xml`` y se firma tal cual
(se parsea con ``lxml`` para que ``signxml`` pueda insertar la firma, pero no
se reserializa el contenido original antes del cálculo del digest de
referencia — ``signxml`` opera directamente sobre el árbol parseado).

--- Nota de canonicalización (por qué el DigestValue no reconcilia con xml_sha256) ---
El ``DigestValue`` de la referencia principal en ``<ds:SignedInfo>`` se calcula
por ``signxml`` aplicando **canonicalización exclusiva** (exc-c14n) sobre el
documento, NO la canonicalización C14N 2.0 de la librería estándar
(``xml.etree.ElementTree.canonicalize``) que usó ``index_builder.build_xml``
para producir ``xml_sha256`` antes de llegar aquí. Son dos algoritmos de
canonicalización distintos (C14N 2.0 vs. Exclusive C14N 1.0) — el
``DigestValue`` XAdES NUNCA coincidirá con ``xml_sha256``, y **no se
reconcilian**: ``xml_sha256``/``payload_sha256`` quedan como huella de
EXHIBICIÓN pre-firma (para detectar alteración del contenido ANTES de firmar,
o para mostrarla en UI); la integridad AUTORITATIVA post-firma es la propia
firma XAdES, verificable con :func:`verify_xades`.

--- Nota de compatibilidad con signxml (bug de referencia por defecto) ---
``signxml`` (validado con 5.0.1) tiene una particularidad en su implementación
de XAdES: las referencias XAdES adicionales (``SignedProperties``, ``KeyInfo``)
NO llevan un elemento ``<ds:Transforms>`` explícito en la firma generada. Al
verificar, si una referencia no tiene ``Transforms``, ``XMLVerifier`` usa
``SignatureConfiguration.default_reference_c14n_method`` — que por defecto es
C14N 1.1 (**no** el algoritmo configurado en el `XAdESSigner`) — en vez del
algoritmo realmente usado al firmar. Con canonicalización exclusiva
configurada (nuestro caso), esto produce un falso ``InvalidDigest`` incluso al
verificar una firma propia sin alterar. El workaround (ver :func:`verify_xades`)
es pasar un ``expect_config`` con ``default_reference_c14n_method`` igualado
explícitamente al mismo algoritmo exclusivo usado al firmar — así los
`DigestValue` de esas referencias se recalculan con el algoritmo correcto.
Verificado con un script de humo end-to-end (firma + verificación + alteración
+ fingerprint distinto + certificado caducado) antes de fijar este diseño.
"""

from __future__ import annotations

import hashlib
from base64 import b64decode
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from lxml import etree
from signxml import CanonicalizationMethod, DigestAlgorithm, SignatureMethod
from signxml.xades import XAdESSignatureConfiguration, XAdESSigner, XAdESVerifier

from app.core.keys import cert_fingerprint
from app.providers.base import PreparedDoc, SignatureFormat, SignedDoc, SignerRef

_SIGN_ALG = SignatureMethod.RSA_SHA256
_DIGEST_ALG = DigestAlgorithm.SHA256
_C14N_ALG = CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0
_DS_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}


def _safe_parser() -> etree.XMLParser:
    """Parser lxml endurecido (RT-16, hardening XXE/DoS).

    ``resolve_entities=False`` + ``load_dtd=False`` + ``no_network=True``
    evitan tanto la resolución de entidades externas/``SYSTEM`` (XXE clásico)
    como el acceso de red al resolver un DTD externo; ``huge_tree=False``
    (default, explícito aquí) limita la profundidad/tamaño para frenar un
    "billion laughs"/expansión cuadrática. Se usa tanto en :func:`verify_xades`
    (el endpoint público ``/verify`` parsea XML enteramente controlado por el
    llamante) como en :meth:`LocalSigner.sign` (mitigado también por el gate
    ``PERM_FIRMA`` de ``/sign-xml``, pero se aplica el mismo parser por
    defensa en profundidad).
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def _parse_no_doctype(xml_bytes: bytes) -> etree._Element:
    """Parsea con :func:`_safe_parser` y rechaza cualquier DOCTYPE (fail-closed).

    El parser endurecido ya impide resolver entidades externas/``SYSTEM``, pero
    sigue aceptando un DOCTYPE con entidades puramente INTERNAS (el vector
    "billion laughs") sin expandirlas al no encontrarlas referenciadas —el
    daño real requeriría que el documento además *use* esas entidades, pero
    ningún documento legítimo de este servicio (índice generado por
    archive-service, o el XML ya firmado que se reenvía a ``/verify``) declara
    DOCTYPE, así que su sola presencia es indicio de un payload adversarial y
    se rechaza aquí explícitamente, sin intentar distinguir "inocuo" de
    "malicioso".

    Raises:
        etree.XMLSyntaxError: XML mal formado.
        ValueError: el documento declara DOCTYPE.
    """
    root = etree.fromstring(xml_bytes, parser=_safe_parser())
    if root.getroottree().docinfo.doctype:
        raise ValueError("dtd_no_permitido")
    return root

# Ver la nota "compatibilidad con signxml" en el docstring del módulo: sin este
# override, `XAdESVerifier().verify()` rechaza incluso una firma propia sin
# alterar cuando el signer usa canonicalización exclusiva.
_VERIFY_CONFIG = replace(XAdESSignatureConfiguration(), default_reference_c14n_method=_C14N_ALG)


@dataclass(slots=True)
class VerifyOutcome:
    """Resultado de :func:`verify_xades` — nunca lanza para fallos de negocio."""

    valida: bool
    acreditado: bool
    motivo: str | None = None


class LocalSigner:
    """Proveedor ``xades_local``: XAdES-B enveloped con el sello institucional."""

    async def prepare(
        self,
        xml_bytes: bytes,
        *,
        objeto_tipo: str,
        objeto_id: str | None = None,
        indice_id: str | None = None,
        version: int | None = None,
    ) -> PreparedDoc:
        return PreparedDoc(
            xml_bytes=xml_bytes,
            objeto_tipo=objeto_tipo,
            objeto_id=objeto_id,
            indice_id=indice_id,
            version=version,
        )

    async def sign(self, prepared: PreparedDoc, signer: SignerRef) -> SignedDoc:
        # Huella de EXHIBICIÓN pre-firma — ver nota de canonicalización arriba.
        payload_sha256 = hashlib.sha256(prepared.xml_bytes).hexdigest()

        root = _parse_no_doctype(prepared.xml_bytes)
        xades_signer = XAdESSigner(
            signature_algorithm=_SIGN_ALG,
            digest_algorithm=_DIGEST_ALG,
            c14n_algorithm=_C14N_ALG,
        )
        signed_root = xades_signer.sign(
            root,
            key=signer.key_pem,
            passphrase=signer.key_password,
            cert=signer.cert_pem,
        )
        signed_xml = etree.tostring(signed_root, xml_declaration=False, encoding="UTF-8")

        sig_value_el = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
        signature_value_b64 = (sig_value_el.text or "") if sig_value_el is not None else ""

        return SignedDoc(
            signed_xml=signed_xml,
            payload_sha256=payload_sha256,
            signature_value_b64=signature_value_b64,
            certificado_pem=signer.cert_pem.decode(),
            xades_level=SignatureFormat.XADES_B.value,
            nivel_conformidad=signer.nivel_conformidad,
            provider="xades_local",
            certificado_fingerprint=cert_fingerprint(signer.cert_pem),
        )

    async def check_status(self, ref: str) -> str:
        """La firma se produce de forma síncrona dentro de :meth:`sign` — siempre SIGNED."""
        return "SIGNED"

    async def download(self, ref: str) -> bytes:
        raise NotImplementedError(
            "LocalSigner no delega custodia externa — el XML firmado ya se "
            "devuelve inline en SignedDoc.signed_xml desde sign()."
        )


def verify_xades(signed_xml: bytes, expected_fingerprint: str) -> VerifyOutcome:
    """Verifica una firma XAdES-B enveloped (E06, Inc.1, RF-FIR-15).

    Valida, en orden (fail-closed: cualquier fallo devuelve ``valida=False``
    con un ``motivo`` explicativo — nunca lanza para casos de negocio):

      1. Validez criptográfica de la firma (``XAdESVerifier.verify``).
      2. Que la referencia principal cubre el elemento raíz completo
         (``<indice>``) — evita que una firma sobre un fragmento se reporte
         como cobertura del documento completo.
      3. Ventana de vigencia del certificado embebido (``notBefore``/``notAfter``).
      4. Que la huella del certificado embebido coincide con
         ``expected_fingerprint`` — el ``certificado_fingerprint`` persistido
         en la fila ``firma`` al momento de firmar (NO se relee el sello
         actual del tenant desde el filesystem: este endpoint es público y no
         debe depender de secretos montados ni de si el sello rotó desde
         entonces; el registro histórico es la fuente de verdad).

    NO valida cadena de confianza contra una CA raíz ni CRL/OCSP (XAdES-LT/LTA,
    fuera de alcance del Incremento 1 — RT-16): por eso ``acreditado`` es
    siempre ``False`` en el resultado, incluso cuando la firma es
    criptográficamente válida (RF-FIR-15: no sobre-declarar conformidad).
    """
    try:
        root = _parse_no_doctype(signed_xml)
    except etree.XMLSyntaxError as exc:
        return VerifyOutcome(False, False, f"xml_invalido: {exc}")
    except ValueError:
        return VerifyOutcome(False, False, "dtd_no_permitido")

    cert_el = root.find(
        ".//ds:Signature/ds:KeyInfo/ds:X509Data/ds:X509Certificate", namespaces=_DS_NS
    )
    if cert_el is None or not cert_el.text:
        return VerifyOutcome(False, False, "sin_certificado_embebido")

    try:
        embedded_cert = x509.load_der_x509_certificate(b64decode(cert_el.text.strip()))
    except Exception as exc:  # noqa: BLE001 — certificado corrupto, fail-closed
        return VerifyOutcome(False, False, f"certificado_ilegible: {exc}")

    now = datetime.now(UTC)
    if not (embedded_cert.not_valid_before_utc <= now <= embedded_cert.not_valid_after_utc):
        return VerifyOutcome(False, False, "certificado_fuera_de_vigencia")

    embedded_fingerprint = embedded_cert.fingerprint(hashes.SHA256()).hex()
    if embedded_fingerprint != expected_fingerprint:
        return VerifyOutcome(False, False, "fingerprint_no_coincide_sello_tenant")

    try:
        results = XAdESVerifier().verify(
            root, x509_cert=embedded_cert, expect_config=_VERIFY_CONFIG
        )
    except Exception as exc:  # noqa: BLE001 — cualquier fallo == firma inválida (fail-closed)
        return VerifyOutcome(False, False, f"firma_invalida: {exc}")

    # (2) La referencia principal debe cubrir la raíz completa del documento.
    covers_root = any(
        r.signed_xml is not None and r.signed_xml.tag == root.tag for r in results
    )
    if not covers_root:
        return VerifyOutcome(False, False, "referencia_no_cubre_raiz")

    return VerifyOutcome(True, False, None)
