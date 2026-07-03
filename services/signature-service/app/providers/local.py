# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""LocalSigner — proveedor XAdES-B/-T enveloped local, vía ``signxml`` (E06, Inc.1/4).

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

--- XAdES-T (E06, Inc.4, RT-16) ---
Con ``settings.signer_timestamp_enabled=True`` (default), tras
``XAdESSigner.sign`` se sella el ``ds:SignatureValue`` recién producido con un
token RFC 3161 (``app.core.tsa.build_timestamp_token``, canonicalizado con el
MISMO algoritmo exclusivo ``_C14N_ALG``) y se incrusta como
``xades:UnsignedProperties/xades:UnsignedSignatureProperties/
xades:SignatureTimeStamp`` — una propiedad NO FIRMADA (no cubierta por
ninguna ``ds:Reference`` de ``SignedInfo``), así que insertarla DESPUÉS de que
``signxml`` ya calculó y firmó todas las referencias no invalida la firma
XAdES-B subyacente. ``SignedDoc.xades_level`` sube a ``"XAdES-T"``. Con el
kill-switch en ``False``, o si ninguna TSA está disponible, ver
:func:`LocalSigner.sign` — este último caso hace FALLAR la firma completa
(fail-closed, igual que la ausencia de sello institucional en Inc.1), NO
degrada en silencio a XAdES-B.
"""

from __future__ import annotations

import hashlib
import uuid
from base64 import b64decode, b64encode
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from lxml import etree
from signxml import CanonicalizationMethod, DigestAlgorithm, SignatureMethod
from signxml.xades import XAdESSignatureConfiguration, XAdESSigner, XAdESVerifier

from app.core import tsa
from app.core.config import settings
from app.core.keys import cert_fingerprint
from app.providers.base import PreparedDoc, SignatureFormat, SignedDoc, SignerRef

_SIGN_ALG = SignatureMethod.RSA_SHA256
_DIGEST_ALG = DigestAlgorithm.SHA256
_C14N_ALG = CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0
_DS_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}
_XADES_NS_URI = "http://uri.etsi.org/01903/v1.3.2#"
_XADES_NS = {"xades": _XADES_NS_URI}


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
    # --- XAdES-T (Inc.4) — None cuando la firma no lleva SignatureTimeStamp
    # (XAdES-B "puro", p. ej. kill-switch signer_timestamp_enabled=False en
    # el momento de firmar). ``sello_tiempo_valido`` solo se evalúa cuando la
    # firma XAdES-B de base ya validó (``valida=True``) — ver verify_xades.
    sello_tiempo_valido: bool | None = None
    tsa_timestamp: datetime | None = None


def _c14n_element(el: etree._Element) -> bytes:
    """Canonicaliza UN elemento (exc-c14n 1.0) — usado para sellar/verificar
    ``ds:SignatureValue`` (Inc.4, ``app.core.tsa``). Determinista: lxml
    reconstruye las declaraciones de namespace "visiblemente utilizadas" por
    el subárbol (incluidas las heredadas de ancestros), así que el resultado
    es el mismo tanto sobre el árbol en memoria recién firmado como sobre un
    XML re-parseado desde bytes."""
    return etree.tostring(el, method="c14n", exclusive=True)


def _insert_signature_timestamp(signed_root: etree._Element, token: tsa.TimestampToken) -> None:
    """Incrusta ``xades:UnsignedProperties/.../xades:SignatureTimeStamp``.

    Propiedad NO FIRMADA (RT-16, ver nota de módulo "XAdES-T"): se añade como
    HERMANA de ``xades:SignedProperties``, dentro de la MISMA
    ``xades:QualifyingProperties`` que ``signxml`` ya produjo — nunca toca
    ``ds:SignedInfo`` ni ninguna ``ds:Reference``, así que no invalida la
    firma XAdES-B de base.
    """
    qp = signed_root.find(".//xades:QualifyingProperties", namespaces=_XADES_NS)
    if qp is None:  # pragma: no cover — signxml siempre produce QualifyingProperties
        raise ValueError("qualifying_properties_no_encontrado")

    ts_id = f"SignXMLTimeStamp{uuid.uuid4().hex[:8].upper()}"
    token_b64 = b64encode(token.token_der).decode("ascii")

    # Construido vía la API de elementos de lxml (no etree.fromstring sobre un
    # fragmento interpolado por f-string): aunque hoy todo el contenido
    # interpolado lo produce el propio servidor (base64 del token + uuid hex +
    # literales, nunca markup ajeno), usar la misma postura endurecida
    # (_safe_parser) que el resto del módulo evita depender de esa garantía
    # como único control — defensa en profundidad uniforme.
    unsigned_props = etree.SubElement(qp, f"{{{_XADES_NS_URI}}}UnsignedProperties")
    unsigned_sig_props = etree.SubElement(
        unsigned_props, f"{{{_XADES_NS_URI}}}UnsignedSignatureProperties"
    )
    sig_ts = etree.SubElement(unsigned_sig_props, f"{{{_XADES_NS_URI}}}SignatureTimeStamp")
    sig_ts.set("Id", ts_id)
    c14n_method = etree.SubElement(sig_ts, f"{{{_DS_NS['ds']}}}CanonicalizationMethod")
    c14n_method.set("Algorithm", _C14N_ALG.value)
    encapsulated_ts = etree.SubElement(sig_ts, f"{{{_XADES_NS_URI}}}EncapsulatedTimeStamp")
    encapsulated_ts.text = token_b64


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

        sig_value_el = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
        signature_value_b64 = (sig_value_el.text or "") if sig_value_el is not None else ""

        xades_level = SignatureFormat.XADES_B.value
        extra: dict = {}
        if settings.signer_timestamp_enabled and sig_value_el is not None:
            # Fail-closed (RT-16, mismo patrón que la ausencia de sello
            # institucional en Inc.1): si el kill-switch está en True, un
            # error de TSA (tsa.TsaUnavailableError) DEBE tumbar la firma
            # completa, no degradar en silencio a XAdES-B — se propaga tal
            # cual al llamador (app.routers.signature lo traduce a 503).
            token = tsa.build_timestamp_token(_c14n_element(sig_value_el))
            _insert_signature_timestamp(signed_root, token)
            xades_level = SignatureFormat.XADES_T.value
            extra = {
                "tsa_token": b64encode(token.token_der).decode("ascii"),
                "tsa_timestamp": token.gen_time.isoformat(),
                "tsa_cert_fingerprint": token.tsa_cert_fingerprint,
                # RF-FIR-15: TSA local sin CA acreditada ONAC — nunca True.
                "tsa_acreditado": False,
            }

        signed_xml = etree.tostring(signed_root, xml_declaration=False, encoding="UTF-8")

        return SignedDoc(
            signed_xml=signed_xml,
            payload_sha256=payload_sha256,
            signature_value_b64=signature_value_b64,
            certificado_pem=signer.cert_pem.decode(),
            xades_level=xades_level,
            nivel_conformidad=signer.nivel_conformidad,
            provider="xades_local",
            certificado_fingerprint=cert_fingerprint(signer.cert_pem),
            extra=extra,
        )

    async def check_status(self, ref: str) -> str:
        """La firma se produce de forma síncrona dentro de :meth:`sign` — siempre SIGNED."""
        return "SIGNED"

    async def download(self, ref: str) -> bytes:
        raise NotImplementedError(
            "LocalSigner no delega custodia externa — el XML firmado ya se "
            "devuelve inline en SignedDoc.signed_xml desde sign()."
        )


def verify_xades(
    signed_xml: bytes, expected_fingerprint: str, expected_tsa_fingerprint: str | None = None
) -> VerifyOutcome:
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

    (Inc.4, XAdES-T) SI las comprobaciones 1-4 anteriores pasan Y la firma
    lleva ``xades:SignatureTimeStamp``, además verifica el sello de tiempo
    (``app.core.tsa.verify_timestamp_token``) y lo refleja en
    ``VerifyOutcome.sello_tiempo_valido``/``tsa_timestamp``. Una firma
    XAdES-B "pura" (sin SignatureTimeStamp, p. ej. firmada con el kill-switch
    ``signer_timestamp_enabled=False``) deja ``sello_tiempo_valido=None`` —
    no aplica, no es un fallo.

    ``expected_tsa_fingerprint`` es ``firma.tsa_cert_fingerprint`` (migración
    012), la ancla de confianza del sello — ``SignatureTimeStamp`` es una
    propiedad NO FIRMADA (ver módulo, sección "XAdES-T"), así que sin cotejar
    contra este valor persistido, cualquiera con un XML XAdES-B genuino del
    tenant podría anexarle un token sellado con su propia TSA autofirmada y
    reportarlo válido con un ``genTime`` arbitrario. Si la firma lleva
    ``SignatureTimeStamp`` pero ``expected_tsa_fingerprint`` es ``None``
    (fila anterior a la migración 012, sin la columna poblada), el sello se
    reporta inválido — fail-closed, nunca se omite la comprobación.
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

    # (Inc.4) Sello de tiempo, si lo hay — ver docstring "XAdES-T" arriba.
    # Se recalcula sobre el ds:SignatureValue TAL COMO llega en este
    # `signed_xml` (no el de la fila BD), así que una alteración posterior al
    # sellado se detecta aquí igual que la detectaría el propio TSTInfo.
    encapsulated_ts = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        "/xades:SignatureTimeStamp/xades:EncapsulatedTimeStamp",
        namespaces=_XADES_NS,
    )
    if encapsulated_ts is None or not (encapsulated_ts.text or "").strip():
        return VerifyOutcome(True, False, None)

    sig_value_el = root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
    if sig_value_el is None:  # pragma: no cover — imposible si la firma XAdES-B ya validó
        return VerifyOutcome(True, False, None, sello_tiempo_valido=False)

    try:
        token_der = b64decode(encapsulated_ts.text.strip())
    except Exception:  # noqa: BLE001 — base64 corrupto, fail-closed
        return VerifyOutcome(True, False, None, sello_tiempo_valido=False)

    ts_outcome = tsa.verify_timestamp_token(
        token_der, _c14n_element(sig_value_el), expected_tsa_fingerprint=expected_tsa_fingerprint
    )
    return VerifyOutcome(
        True,
        False,
        None,
        sello_tiempo_valido=ts_outcome.valido,
        tsa_timestamp=ts_outcome.gen_time if ts_outcome.valido else None,
    )
