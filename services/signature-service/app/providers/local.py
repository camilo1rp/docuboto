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

--- XAdES-LT/LTA (E06, RT-16) — material de validación a largo plazo ---
Con ``settings.signer_lt_enabled=True`` Y el sello institucional del tenant
acompañado de material de una CA local de desarrollo (``ca.crt``/``crl.der``,
co-ubicados por ``app.core.keys.load_seal`` — ver ``scripts/gen-dev-ca.sh``),
la firma XAdES-T se escala en el MISMO acto a XAdES-LTA: se añaden, dentro de
la misma ``xades:UnsignedSignatureProperties``, en ORDEN ETSI EN 319 132
(``SignatureTimeStamp`` ya insertado, luego):

  1. ``xades:CertificateValues`` — SOLO el certificado de la CA (el del sello
     ya viaja en ``ds:KeyInfo``, no se duplica aquí).
  2. ``xades:RevocationValues`` — la CRL de la CA (``xades:CRLValues``).
  3. ``xadesv141:ArchiveTimeStamp`` (namespace v1.4.1, distinto de v1.3.2) —
     SIEMPRE el ÚLTIMO: un sello RFC 3161 (misma TSA que XAdES-T) sobre la
     concatenación C14N-exclusiva de un conjunto FIJO y determinista de
     elementos precedentes (ver :func:`_archive_ts_covered_bytes` — build y
     verify comparten la MISMA función; cualquier divergencia rompe la
     verificación).

LT y LTA se producen SIEMPRE juntos al firmar (nunca queda una firma "solo
LT" persistida) — la distinción entre ambos niveles solo emerge en
:func:`verify_xades` si el ``ArchiveTimeStamp`` no llega a validar pero el
resto del material LT sí. Sin material CA montado, la firma se queda en
XAdES-T: degradación OBSERVABLE (``firma.lt_downgrade_no_ca`` en
``app.routers.signature``), nunca silenciosa. Perfil "LTA SIMPLIFICADO, NO
ACREDITADO" (RF-FIR-15): ``acreditado`` sigue SIEMPRE ``False`` — la CA es de
desarrollo, sin acreditación ONAC, y la propia TSA que sella el
``ArchiveTimeStamp`` es autofirmada (no emitida por esa CA).

--- OCSP stapled en XAdES-LT (E06, RF-FIR-15) ---
Con ``settings.signer_ocsp_enabled=True`` (default ``False``) Y el tenant con
material de responder OCSP delegado montado (``ocsp.crt``/``ocsp.key``,
``app.core.keys.load_seal`` — ``scripts/gen-dev-ocsp-responder.sh``), el
mismo bloque XAdES-LT añade ``xades:OCSPValues/xades:EncapsulatedOCSPValue``
como HIJO del ``xades:RevocationValues`` YA creado por
:func:`_insert_revocation_values` — DESPUÉS de ``xades:CRLValues``, ANTES de
``_insert_archive_timestamp`` (así el ``ArchiveTimeStamp`` lo cubre sin tocar
:func:`_archive_ts_covered_bytes`, que canonicaliza ``RevocationValues``
completo — CRL Y OCSP, ver ``app.core.ocsp``). Es ADITIVO a la CRL, nunca la
reemplaza: XAdES-LT sigue exigiendo ``ca.crt``/``crl.der`` montados; sin
material CA no hay bloque LT y por lo tanto tampoco OCSP, sin importar este
flag. Sin material del responder OCSP, la firma XAdES-LT/LTA se produce
igual (degradación OBSERVABLE — ``firma.ocsp_downgrade_no_responder`` en
``app.routers.signature``), nunca en silencio ni bloqueante salvo
``settings.signer_ocsp_require=True``. OCSP nunca cambia ``xades_level`` ni
``acreditado`` — ver ``app.core.ocsp`` para el detalle de construcción y
verificación de la ``BasicOCSPResponse``.
"""

from __future__ import annotations

import hashlib
import uuid
from base64 import b64decode, b64encode
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from lxml import etree
from signxml import CanonicalizationMethod, DigestAlgorithm, SignatureMethod
from signxml.xades import XAdESSignatureConfiguration, XAdESSigner, XAdESVerifier

from app.core import ocsp, tsa
from app.core.config import settings
from app.core.keys import cert_fingerprint
from app.providers.base import PreparedDoc, SignatureFormat, SignedDoc, SignerRef

_SIGN_ALG = SignatureMethod.RSA_SHA256
_DIGEST_ALG = DigestAlgorithm.SHA256
_C14N_ALG = CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0
_DS_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}
_XADES_NS_URI = "http://uri.etsi.org/01903/v1.3.2#"
_XADES_NS = {"xades": _XADES_NS_URI}
# XAdES-LTA (v1.4.1): namespace DISTINTO de v1.3.2 — SOLO el ArchiveTimeStamp
# "moderno" vive aquí (RT-16, ver docstring de módulo "XAdES-LT/LTA").
_XADESV141_NS_URI = "http://uri.etsi.org/01903/v1.4.1#"
_XADESV141_NS = {"xadesv141": _XADESV141_NS_URI}
_LT_NS = {**_XADES_NS, **_XADESV141_NS}


class LtMaterialRequiredError(RuntimeError):
    """``settings.signer_lt_require=True`` pero el tenant no tiene material de
    CA local (``ca.crt``/``crl.der``) montado junto a su sello institucional
    (RT-16, Pieza 3) — fail-closed, el router lo traduce a 503.
    """


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
    # --- XAdES-LT/LTA — nivel REALMENTE verificado (RT-16, Pieza 6.4): nunca
    # el nivel "intencionado" al firmar, sino el que esta comprobación pudo
    # validar de verdad para ESTE ``signed_xml`` concreto. ``None`` solo si
    # ``valida=False`` antes de llegar siquiera a evaluar el nivel (p. ej.
    # certificado fuera de vigencia) — el llamador (``app.routers.signature``)
    # entonces cae al ``xades_level`` persistido en la fila.
    xades_level: str | None = None
    lt_material_present: bool = False
    archive_timestamp_present: bool = False
    revocation_provenance: str | None = None
    crl_next_update: datetime | None = None
    # --- OCSP stapled en XAdES-LT (RF-FIR-15) — False/None cuando la firma no
    # lleva xades:OCSPValues (retrocompat: XAdES-LT/LTA sin OCSP, o el
    # kill-switch signer_ocsp_enabled=False en el momento de firmar). Solo se
    # evalúa dentro del camino LT/LTA (después de que CRL/cadena ya validaron)
    # — ver verify_xades.
    ocsp_present: bool = False
    ocsp_cert_status: str | None = None


def _c14n_element(el: etree._Element) -> bytes:
    """Canonicaliza UN elemento (exc-c14n 1.0) — usado para sellar/verificar
    ``ds:SignatureValue`` (Inc.4, ``app.core.tsa``). Determinista: lxml
    reconstruye las declaraciones de namespace "visiblemente utilizadas" por
    el subárbol (incluidas las heredadas de ancestros), así que el resultado
    es el mismo tanto sobre el árbol en memoria recién firmado como sobre un
    XML re-parseado desde bytes."""
    return etree.tostring(el, method="c14n", exclusive=True)


def _get_or_create_unsigned_sig_props(signed_root: etree._Element) -> etree._Element:
    """Localiza (o crea, la PRIMERA vez) ``xades:UnsignedProperties/
    xades:UnsignedSignatureProperties`` dentro de la ``xades:QualifyingProperties``
    que ``signxml`` ya produjo (RT-16, Pieza 4).

    Punto único de entrada compartido por :func:`_insert_signature_timestamp`
    (XAdES-T), :func:`_insert_certificate_values`/:func:`_insert_revocation_values`
    (XAdES-LT) y :func:`_insert_archive_timestamp` (XAdES-LTA): cada llamada
    posterior reutiliza el MISMO elemento en vez de crear un
    ``UnsignedProperties`` duplicado, así que el orden ETSI EN 319 132 entre
    las propiedades queda determinado exclusivamente por el ORDEN DE LLAMADA
    de ``LocalSigner.sign`` (``etree.SubElement`` siempre añade al final).

    Ninguna de estas propiedades está FIRMADA (no hay ``ds:Reference`` que las
    cubra en ``ds:SignedInfo``), así que insertarlas después de que ``signxml``
    ya calculó y firmó todas las referencias no invalida la firma XAdES-B de
    base — ver la nota de módulo "XAdES-T".
    """
    qp = signed_root.find(".//xades:QualifyingProperties", namespaces=_XADES_NS)
    if qp is None:  # pragma: no cover — signxml siempre produce QualifyingProperties
        raise ValueError("qualifying_properties_no_encontrado")

    # Construido vía la API de elementos de lxml (no etree.fromstring sobre un
    # fragmento interpolado por f-string): aunque hoy todo el contenido
    # interpolado lo produce el propio servidor (base64 del token/certificado/
    # CRL + uuid hex + literales, nunca markup ajeno), usar la misma postura
    # endurecida (_safe_parser) que el resto del módulo evita depender de esa
    # garantía como único control — defensa en profundidad uniforme.
    unsigned_props = qp.find("xades:UnsignedProperties", namespaces=_XADES_NS)
    if unsigned_props is None:
        unsigned_props = etree.SubElement(qp, f"{{{_XADES_NS_URI}}}UnsignedProperties")
    unsigned_sig_props = unsigned_props.find(
        "xades:UnsignedSignatureProperties", namespaces=_XADES_NS
    )
    if unsigned_sig_props is None:
        unsigned_sig_props = etree.SubElement(
            unsigned_props, f"{{{_XADES_NS_URI}}}UnsignedSignatureProperties"
        )
    return unsigned_sig_props


def _insert_signature_timestamp(signed_root: etree._Element, token: tsa.TimestampToken) -> None:
    """Incrusta ``xades:SignatureTimeStamp`` (XAdES-T, RT-16, Inc.4). Ver
    :func:`_get_or_create_unsigned_sig_props` para el punto de inserción."""
    unsigned_sig_props = _get_or_create_unsigned_sig_props(signed_root)

    ts_id = f"SignXMLTimeStamp{uuid.uuid4().hex[:8].upper()}"
    sig_ts = etree.SubElement(unsigned_sig_props, f"{{{_XADES_NS_URI}}}SignatureTimeStamp")
    sig_ts.set("Id", ts_id)
    c14n_method = etree.SubElement(sig_ts, f"{{{_DS_NS['ds']}}}CanonicalizationMethod")
    c14n_method.set("Algorithm", _C14N_ALG.value)
    encapsulated_ts = etree.SubElement(sig_ts, f"{{{_XADES_NS_URI}}}EncapsulatedTimeStamp")
    encapsulated_ts.text = b64encode(token.token_der).decode("ascii")


def _insert_certificate_values(unsigned_sig_props: etree._Element, ca_cert_der: bytes) -> None:
    """Incrusta ``xades:CertificateValues`` (XAdES-LT, Pieza 4).

    SOLO el certificado de la CA — el del sello institucional ya viaja en
    ``ds:KeyInfo/ds:X509Data/ds:X509Certificate`` y EN 319 132 §5.2.9 exige NO
    duplicar certificados ya presentes en ``KeyInfo``.
    """
    cert_values = etree.SubElement(unsigned_sig_props, f"{{{_XADES_NS_URI}}}CertificateValues")
    encapsulated_cert = etree.SubElement(
        cert_values, f"{{{_XADES_NS_URI}}}EncapsulatedX509Certificate"
    )
    encapsulated_cert.text = b64encode(ca_cert_der).decode("ascii")


def _insert_revocation_values(unsigned_sig_props: etree._Element, crl_der: bytes) -> etree._Element:
    """Incrusta ``xades:RevocationValues/xades:CRLValues`` (XAdES-LT, Pieza 4).

    Una única CRL de la CA local de desarrollo (``crl.der``, DER en base64).

    Devuelve el elemento ``xades:RevocationValues`` recién creado — el gate
    OCSP (``settings.signer_ocsp_enabled``, ver :func:`_insert_ocsp_values`)
    lo reutiliza para añadir ``xades:OCSPValues`` como HIJO del MISMO
    ``RevocationValues`` (nunca uno separado), DESPUÉS de ``CRLValues``.
    """
    revocation_values = etree.SubElement(
        unsigned_sig_props, f"{{{_XADES_NS_URI}}}RevocationValues"
    )
    crl_values = etree.SubElement(revocation_values, f"{{{_XADES_NS_URI}}}CRLValues")
    encapsulated_crl = etree.SubElement(crl_values, f"{{{_XADES_NS_URI}}}EncapsulatedCRLValue")
    encapsulated_crl.text = b64encode(crl_der).decode("ascii")
    return revocation_values


def _insert_ocsp_values(revocation_values_el: etree._Element, ocsp_der: bytes) -> None:
    """Incrusta ``xades:OCSPValues/xades:EncapsulatedOCSPValue`` (OCSP stapled
    en XAdES-LT, RF-FIR-15) como HIJO de ``revocation_values_el`` — el MISMO
    ``xades:RevocationValues`` que :func:`_insert_revocation_values` ya pobló
    con ``xades:CRLValues``, DESPUÉS de éste (ADITIVO, nunca lo reemplaza).

    Al insertarse ANTES de :func:`_insert_archive_timestamp`, queda cubierto
    por el ``ArchiveTimeStamp`` sin cambiar
    :func:`_archive_ts_covered_bytes` — esa función ya canonicaliza el
    ``RevocationValues`` completo (CRL y, si está presente, OCSP).
    """
    ocsp_values = etree.SubElement(revocation_values_el, f"{{{_XADES_NS_URI}}}OCSPValues")
    encapsulated_ocsp = etree.SubElement(ocsp_values, f"{{{_XADES_NS_URI}}}EncapsulatedOCSPValue")
    encapsulated_ocsp.text = b64encode(ocsp_der).decode("ascii")


def _archive_ts_covered_bytes(signed_root: etree._Element) -> bytes:
    """Concatena la canonicalización exclusiva (exc-c14n 1.0) del conjunto FIJO
    y determinista que cubre el ``xadesv141:ArchiveTimeStamp`` (XAdES-LTA
    SIMPLIFICADO, Pieza 5), en ESTE orden:

        C14N(ds:SignedInfo) || C14N(ds:SignatureValue) || C14N(ds:KeyInfo) ||
        C14N(xades:SignedProperties) || C14N(xades:SignatureTimeStamp) ||
        C14N(xades:CertificateValues) || C14N(xades:RevocationValues)

    Debe llamarse ANTES de insertar el propio ``ArchiveTimeStamp`` (que cubre
    todo lo anterior, nunca a sí mismo). Usado TANTO al construir
    (``LocalSigner.sign``) COMO al verificar (:func:`verify_xades`) — la MISMA
    función en ambos lados: cualquier no-determinismo (prefijos de namespace,
    espacios) invalidaría la verificación (RT-16, requisito no negociable del
    contrato de este incremento).

    ``ds:SignedInfo`` ya lleva el digest del documento enveloped (referencia
    principal calculada por ``signxml``) — no hace falta re-canonicalizar el
    cuerpo del índice electrónico aparte.

    Raises:
        ValueError: falta alguno de los siete elementos requeridos (firma
            XAdES-B/-T/-LT incompleta o corrupta).
    """
    signed_info = signed_root.find(".//ds:Signature/ds:SignedInfo", namespaces=_DS_NS)
    signature_value = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
    key_info = signed_root.find(".//ds:Signature/ds:KeyInfo", namespaces=_DS_NS)
    qp = signed_root.find(".//xades:QualifyingProperties", namespaces=_XADES_NS)
    signed_props = qp.find("xades:SignedProperties", namespaces=_XADES_NS) if qp is not None else None
    sig_ts = (
        qp.find(
            "xades:UnsignedProperties/xades:UnsignedSignatureProperties/xades:SignatureTimeStamp",
            namespaces=_XADES_NS,
        )
        if qp is not None
        else None
    )
    cert_values = (
        qp.find(
            "xades:UnsignedProperties/xades:UnsignedSignatureProperties/xades:CertificateValues",
            namespaces=_XADES_NS,
        )
        if qp is not None
        else None
    )
    rev_values = (
        qp.find(
            "xades:UnsignedProperties/xades:UnsignedSignatureProperties/xades:RevocationValues",
            namespaces=_XADES_NS,
        )
        if qp is not None
        else None
    )

    required = {
        "ds:SignedInfo": signed_info,
        "ds:SignatureValue": signature_value,
        "ds:KeyInfo": key_info,
        "xades:SignedProperties": signed_props,
        "xades:SignatureTimeStamp": sig_ts,
        "xades:CertificateValues": cert_values,
        "xades:RevocationValues": rev_values,
    }
    missing = [name for name, el in required.items() if el is None]
    if missing:
        raise ValueError(f"elementos_faltantes_para_archive_timestamp: {missing}")

    return b"".join(
        etree.tostring(el, method="c14n", exclusive=True)
        for el in (
            signed_info,
            signature_value,
            key_info,
            signed_props,
            sig_ts,
            cert_values,
            rev_values,
        )
    )


def _insert_archive_timestamp(
    signed_root: etree._Element, unsigned_sig_props: etree._Element
) -> tsa.TimestampToken:
    """Incrusta ``xadesv141:ArchiveTimeStamp`` (XAdES-LTA, Pieza 5) — SIEMPRE
    el ÚLTIMO hijo insertado en ``UnsignedSignatureProperties`` (cubre todo lo
    precedente, ver :func:`_archive_ts_covered_bytes`). Reutiliza
    ``tsa.build_timestamp_token`` (la MISMA TSA que sella XAdES-T) sobre el
    digest de la concatenación cubierta, no sobre ``ds:SignatureValue``.

    Namespace: SOLO el elemento contenedor ``ArchiveTimeStamp`` en sí usa
    v1.4.1 (``xadesv141``, el que distingue el perfil "moderno" del legado
    ``xades:ArchiveTimeStamp`` v1.3.2) — el elemento global
    ``xadesv141:ArchiveTimeStamp`` referencia el MISMO ``XAdESTimeStampType``
    de v1.3.2 que ``SignatureTimeStamp`` (ver ``XAdESv141.xsd``/``XAdES.xsd``
    de ``signxml``), así que sus HIJOS (``CanonicalizationMethod``,
    ``EncapsulatedTimeStamp``) van en el namespace v1.3.2 de siempre — usar
    v1.4.1 también para los hijos rompe la validación XSD de ``signxml``.
    """
    covered = _archive_ts_covered_bytes(signed_root)
    token = tsa.build_timestamp_token(covered)

    ts_id = f"SignXMLArchiveTimeStamp{uuid.uuid4().hex[:8].upper()}"
    archive_ts = etree.SubElement(unsigned_sig_props, f"{{{_XADESV141_NS_URI}}}ArchiveTimeStamp")
    archive_ts.set("Id", ts_id)
    c14n_method = etree.SubElement(archive_ts, f"{{{_DS_NS['ds']}}}CanonicalizationMethod")
    c14n_method.set("Algorithm", _C14N_ALG.value)
    encapsulated_ts = etree.SubElement(archive_ts, f"{{{_XADES_NS_URI}}}EncapsulatedTimeStamp")
    encapsulated_ts.text = b64encode(token.token_der).decode("ascii")
    return token


class LocalSigner:
    """Proveedor ``xades_local``/``xades_personal``: XAdES-B/-T enveloped.

    Generalizado en E17/F4 Incremento 1 para servir DOS actores con el MISMO
    código de construcción XAdES (TSA/LT/OCSP incluidos, sin duplicar nada de
    eso): el sello institucional del tenant (``key_pem`` en proceso,
    ``signer.remote_sign is None`` — comportamiento sin cambios desde Inc.1)
    y la firma PERSONAL por-usuario (``signer.remote_sign`` presente — ver la
    nota extensa en ``app.providers.base.SignerRef``). La rama personal usa
    la técnica de "firma remota" validada en el Paso 0 del incremento: un par
    RSA EFÍMERO desechable solo para que ``signxml`` complete
    ``ds:SignedInfo`` con el certificado REAL del usuario en ``KeyInfo``
    (``always_add_key_value=False`` — sin esto, ``XAdESSigner`` embebe
    también un ``ds:KeyValue`` derivado de la clave EFÍMERA, que
    ``XAdESVerifier`` rechaza por ambigüedad con ``X509Data`` — hallazgo del
    Paso 0); el ``ds:SignatureValue`` real lo produce ``remote_sign`` sobre
    el SHA-256 de ``C14N-exclusiva(ds:SignedInfo)`` — la clave privada del
    usuario NUNCA entra en este proceso."""

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

        if signer.remote_sign is not None:
            # --- Firma PERSONAL PKI por-usuario (E17/F4, Inc.1) — técnica de
            # firma remota validada en el Paso 0 (tests/test_personal_signing_smoke.py) ---
            ephemeral_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ephemeral_pem = ephemeral_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            # `always_add_key_value=False`: sin esto, `XAdESSigner` (que por
            # defecto fija `always_add_key_value=True`, a diferencia de
            # `XMLSigner` base) embebe un `ds:KeyValue` derivado de la clave
            # EFÍMERA junto al `ds:X509Data` del cert REAL — `XAdESVerifier`
            # rechaza esa combinación como ambigua (hallazgo del Paso 0).
            signed_root = xades_signer.sign(
                root,
                key=ephemeral_pem,
                # `.decode()`: PEM es ASCII y `XAdESSigner.sign` declara
                # `cert: str | list[str] | list[Certificate] | None`. Se pasaba
                # `bytes`, fuera del contrato declarado de la librería.
                cert=signer.cert_pem.decode("ascii"),
                always_add_key_value=False,
            )
            del ephemeral_key, ephemeral_pem  # REGLA no-fuga: descartada apenas se usa

            signed_info_el = signed_root.find(
                ".//ds:Signature/ds:SignedInfo", namespaces=_DS_NS
            )
            if signed_info_el is None:  # pragma: no cover — signxml siempre produce SignedInfo
                raise ValueError("signed_info_no_encontrado")
            signed_info_digest = hashlib.sha256(
                etree.tostring(signed_info_el, method="c14n", exclusive=True)
            ).digest()
            real_signature = await signer.remote_sign(signed_info_digest)

            sig_value_el = signed_root.find(
                ".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS
            )
            if sig_value_el is None:  # pragma: no cover — signxml siempre produce SignatureValue
                raise ValueError("signature_value_no_encontrado")
            sig_value_el.text = b64encode(real_signature).decode("ascii")
            signature_value_b64 = sig_value_el.text
        else:
            signed_root = xades_signer.sign(
                root,
                key=signer.key_pem,
                passphrase=signer.key_password,
                cert=signer.cert_pem.decode("ascii"),  # ver nota arriba
            )
            sig_value_el = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
            signature_value_b64 = (sig_value_el.text or "") if sig_value_el is not None else ""

        xades_level = SignatureFormat.XADES_B.value
        nivel_conformidad = signer.nivel_conformidad
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

            # --- XAdES-LT/LTA (RT-16) — ver nota de módulo "XAdES-LT/LTA" ---
            # `signer.remote_sign is None` acota este bloque al SELLO
            # INSTITUCIONAL (Inc.1/4): la firma PERSONAL (`remote_sign`
            # presente) NUNCA debe entrar aquí, aunque también traiga
            # `signer.ca_cert_pem` poblado (Incremento 2 — ver el `elif` de
            # abajo) — este material es la sub-CA de USUARIOS, no la CA
            # local del sello, y nunca lleva `crl_der` (la revocación
            # personal es en línea, no por CRL — RF-FIR-15/E17 Inc.2), así
            # que sin este guard caería en la rama `else` de abajo
            # (`lt_downgrade_no_ca`) y podría hacer FALLAR la firma personal
            # entera si `settings.signer_lt_require=True` está pensado solo
            # para el sello institucional.
            if signer.remote_sign is None and settings.signer_lt_enabled:
                if signer.ca_cert_pem and signer.crl_der:
                    unsigned_sig_props = _get_or_create_unsigned_sig_props(signed_root)
                    ca_cert = x509.load_pem_x509_certificate(signer.ca_cert_pem)
                    ca_der = ca_cert.public_bytes(serialization.Encoding.DER)
                    crl = x509.load_der_x509_crl(signer.crl_der)

                    _insert_certificate_values(unsigned_sig_props, ca_der)
                    rev_el = _insert_revocation_values(unsigned_sig_props, signer.crl_der)

                    # --- OCSP stapled (RF-FIR-15) — ver nota de módulo
                    # "OCSP stapled en XAdES-LT". ADITIVO a la CRL ya
                    # insertada arriba: nunca cambia xades_level/nivel_conformidad
                    # por sí solo, y su ausencia nunca bloquea LT/LTA salvo
                    # settings.signer_ocsp_require=True.
                    if settings.signer_ocsp_enabled:
                        # `responder_key` queda a `None` cuando no hay material,
                        # para que la comprobación de abajo sea UNA sola y no
                        # dependa de qué variables quedaron definidas.
                        responder_key = None
                        if signer.ocsp_cert_pem and signer.ocsp_key_pem:
                            embedded_seal_cert = x509.load_pem_x509_certificate(signer.cert_pem)
                            responder_cert = x509.load_pem_x509_certificate(signer.ocsp_cert_pem)
                            responder_key = serialization.load_pem_private_key(
                                signer.ocsp_key_pem,
                                password=(
                                    settings.signer_ocsp_key_password.encode()
                                    if settings.signer_ocsp_key_password
                                    else None
                                ),
                            )
                        # `load_pem_private_key` devuelve la unión de TODOS los
                        # tipos de clave que soporta `cryptography`, pero
                        # `build_ocsp_response` firma con RSA. Una clave de
                        # respondedor no-RSA es material INUTILIZABLE, no un
                        # error de la firma en curso: se encauza por la misma
                        # degradación observable que el caso "sin respondedor"
                        # —el fail-closed de `signer_ocsp_require` se decide
                        # después, unificado— en vez de reventar la firma con
                        # un error del backend criptográfico.
                        if isinstance(responder_key, rsa.RSAPrivateKey):
                            ocsp_resp = ocsp.build_ocsp_response(
                                embedded_seal_cert, ca_cert, responder_cert, responder_key, crl=crl
                            )
                            _insert_ocsp_values(rev_el, ocsp_resp.response_der)
                            extra.update(
                                {
                                    "ocsp_present": True,
                                    "ocsp_responder_fingerprint": ocsp_resp.responder_cert_fingerprint,
                                    "ocsp_cert_status": ocsp_resp.cert_status,
                                    "ocsp_produced_at": ocsp_resp.produced_at.isoformat(),
                                    "ocsp_this_update": ocsp_resp.this_update.isoformat(),
                                    "ocsp_next_update": (
                                        ocsp_resp.next_update.isoformat() if ocsp_resp.next_update else None
                                    ),
                                    "ocsp_response_der": b64encode(ocsp_resp.response_der).decode("ascii"),
                                    "ocsp_downgrade_no_responder": False,
                                }
                            )
                        else:
                            # Degradación OBSERVABLE (mismo patrón que
                            # lt_downgrade_no_ca): la firma XAdES-LT/LTA se
                            # produce igual, solo sin OCSPValues — el router
                            # audita `firma.ocsp_downgrade_no_responder`. El
                            # fail-closed de `signer_ocsp_require` NO se decide
                            # aquí (ver comprobación unificada tras el bloque
                            # LT completo, que también cubre el caso "sin CA").
                            extra["ocsp_downgrade_no_responder"] = True

                    # SIEMPRE el último — cubre TODO lo insertado hasta aquí
                    # (SignatureTimeStamp + CertificateValues + RevocationValues,
                    # incluido OCSPValues si se insertó arriba).
                    archive_token = _insert_archive_timestamp(signed_root, unsigned_sig_props)

                    xades_level = SignatureFormat.XADES_LTA.value
                    nivel_conformidad = "xades_lta_no_acreditado"
                    extra.update(
                        {
                            "lt_material_present": True,
                            "ca_fingerprint": ca_cert.fingerprint(hashes.SHA256()).hex(),
                            "crl_fingerprint": hashlib.sha256(signer.crl_der).hexdigest(),
                            "crl_this_update": crl.last_update_utc.isoformat(),
                            "crl_next_update": (
                                crl.next_update_utc.isoformat() if crl.next_update_utc else None
                            ),
                            "archive_timestamp_present": True,
                            "archive_timestamp_at": archive_token.gen_time.isoformat(),
                            "archive_tsa_cert_fingerprint": archive_token.tsa_cert_fingerprint,
                            "archive_timestamp_token": b64encode(
                                archive_token.token_der
                            ).decode("ascii"),
                            # Perfil LTA SIMPLIFICADO, NO ACREDITADO (RF-FIR-15,
                            # Pieza 7): la CRL es de una CA de desarrollo, jamás
                            # de una PKI acreditada — se marca la procedencia en
                            # vez de dejarla implícita.
                            "revocation_provenance": "dev",
                            "lt_downgrade_no_ca": False,
                        }
                    )
                else:
                    # Degradación OBSERVABLE (RT-16, Pieza 3): el índice se
                    # queda en XAdES-T, nunca en silencio — el router audita
                    # `firma.lt_downgrade_no_ca` cuando ve este flag.
                    extra["lt_downgrade_no_ca"] = True
                    if settings.signer_lt_require:
                        raise LtMaterialRequiredError(
                            "signer_lt_require=True pero el tenant no tiene material de "
                            "CA local (ca.crt/crl.der) montado junto a su sello institucional"
                        )
            elif signer.remote_sign is not None and signer.ca_cert_pem:
                # --- Anclaje de la sub-CA de USUARIOS (E17/F4, Incremento 2,
                # Baja #4) — firma PERSONAL únicamente. Embebe SOLO
                # `xades:CertificateValues` con el certificado de la sub-CA
                # que emitió el leaf del usuario (`signer.ca_cert_pem`, PEM
                # provisto por auth-service en `sign-digest`, ver
                # `app.services.cadena_service._firmar_personal`) — a
                # PROPÓSITO, SIN el resto del camino LT
                # (`RevocationValues`/`ArchiveTimeStamp`): la revocación de
                # una clave personal se resuelve EN LÍNEA, contra
                # `GET /internal/signing-key/revocation-status`
                # (`verify_xades` NUNCA hace I/O — esa llamada la hace el
                # ROUTER en `/verify`), no por una CRL embebida. `xades_level`
                # se queda en XAdES-T: este material NO escala a "XAdES-LT"
                # (ese nivel implica el paquete LT completo del sello
                # institucional, que aquí nunca se produce).
                unsigned_sig_props = _get_or_create_unsigned_sig_props(signed_root)
                user_ca_cert = x509.load_pem_x509_certificate(signer.ca_cert_pem)
                _insert_certificate_values(
                    unsigned_sig_props,
                    user_ca_cert.public_bytes(serialization.Encoding.DER),
                )
                # MISMA clave que el `extra["ca_fingerprint"]` institucional
                # de arriba (nunca ambas ramas se ejecutan a la vez, ver el
                # `if/elif`) — así `cadena_service._firmar_personal` no
                # necesita distinguir el origen al persistir
                # `firma.ca_fingerprint` (columna EXISTENTE, reutilizada,
                # misma semántica de "ancla de confianza de la CA/sub-CA
                # embebida" en ambos casos).
                extra["ca_fingerprint"] = user_ca_cert.fingerprint(hashes.SHA256()).hex()

            # --- OCSP obligatorio (RF-FIR-15) — comprobación UNIFICADA ---
            # `signer_ocsp_require=True` presupone que hay LT (OCSP es
            # aditivo a LT, ver docstring de módulo "OCSP stapled en
            # XAdES-LT"): si NO se produjo una respuesta OCSP real
            # (`extra["ocsp_present"]`), da igual la causa — falta material de
            # CA local (rama `else` de arriba, `lt_downgrade_no_ca`) o falta
            # el par de responder OCSP (`ocsp_downgrade_no_responder`) — debe
            # fallar cerrado igual. Colocarla FUERA del bloque `if
            # signer.ca_cert_pem and signer.crl_der:` es lo que cubre AMBOS
            # caminos de degradación en un solo lugar (antes solo el segundo
            # disparaba el 503). `signer.remote_sign is None`: este
            # requisito es exclusivo del sello institucional — la firma
            # personal nunca produce (ni necesita) OCSP stapled.
            if (
                signer.remote_sign is None
                and settings.signer_ocsp_enabled
                and settings.signer_ocsp_require
                and not extra.get("ocsp_present")
            ):
                raise ocsp.OcspMaterialRequiredError(
                    "signer_ocsp_require=True pero no se produjo respuesta OCSP "
                    "(falta material de CA local o de responder OCSP)"
                )

        signed_xml = etree.tostring(signed_root, xml_declaration=False, encoding="UTF-8")

        return SignedDoc(
            signed_xml=signed_xml,
            payload_sha256=payload_sha256,
            signature_value_b64=signature_value_b64,
            certificado_pem=signer.cert_pem.decode(),
            xades_level=xades_level,
            nivel_conformidad=nivel_conformidad,
            provider="xades_personal" if signer.remote_sign is not None else "xades_local",
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
    signed_xml: bytes,
    expected_fingerprint: str,
    expected_tsa_fingerprint: str | None = None,
    expected_ca_fingerprint: str | None = None,
    expected_ocsp_responder_fingerprint: str | None = None,
    expected_user_ca_fingerprint: str | None = None,
) -> VerifyOutcome:
    """Verifica una firma XAdES-B/-T/-LT/-LTA enveloped (E06, RF-FIR-15).

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

    ``acreditado`` es SIEMPRE ``False`` en el resultado, incluso cuando la
    firma es criptográficamente válida y sube hasta XAdES-LTA (RF-FIR-15: la
    CA local de desarrollo no tiene acreditación ONAC — ver la nota de módulo
    "XAdES-LT/LTA").

    (XAdES-T) SI las comprobaciones 1-4 anteriores pasan Y la firma lleva
    ``xades:SignatureTimeStamp``, además verifica el sello de tiempo
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

    (XAdES-LT/LTA) SI el sello de tiempo validó Y la firma lleva
    ``xades:CertificateValues``, además valida (fail-closed, cualquier fallo
    de aquí en adelante hace ``valida=False`` — a diferencia del sello de
    tiempo "puro" ausente, el material LT presente-pero-inválido SÍ invalida
    la firma completa):

      a. La huella del certificado de la CA embebido coincide con
         ``expected_ca_fingerprint`` — ``firma.ca_fingerprint`` (migración
         013), el ANCLA DE CONFIANZA a la CA DEL TENANT capturada al firmar
         (mismo patrón de pinning que ``expected_tsa_fingerprint``: sin este
         cotejo, cualquiera podría anexar CertificateValues de SU PROPIA CA).
      b. La CA tiene ``basicConstraints CA:TRUE`` y ``keyUsage keyCertSign``,
         y está vigente.
      c. El certificado del sello (``embedded_cert``) fue REALMENTE emitido
         por esa CA (``Certificate.verify_directly_issued_by``) — la cadena
         seal→CA (RT-16, Pieza 6.1).
      d. La CRL embebida (``xades:RevocationValues``) está firmada por la
         MISMA CA y el serial del sello NO aparece revocado — fail-closed si
         la firma de la CRL no valida o si el serial SÍ está revocado
         (RT-16, Pieza 6.2). ``nextUpdate`` vencido se EXPONE
         (``VerifyOutcome.crl_next_update``) pero NO invalida por sí solo la
         firma en este perfil no acreditado (las CRL de desarrollo caducan).
      e. (OCSP stapled, RF-FIR-15) SI la firma lleva
         ``xades:RevocationValues/xades:OCSPValues``, se verifica la
         ``BasicOCSPResponse`` embebida (``app.core.ocsp.verify_ocsp_response``,
         pinning contra ``expected_ocsp_responder_fingerprint`` —
         ``firma.ocsp_responder_fingerprint``) — fail-closed si no valida.
         AUSENTE: sin cambios (retrocompat exacta, XAdES-LT válida sobre CRL
         sola). Nunca cambia ``xades_level``/``acreditado`` por sí solo — solo
         puebla ``VerifyOutcome.ocsp_present``/``ocsp_cert_status``.

    Si además hay ``xadesv141:ArchiveTimeStamp``, se reconstruye el conjunto
    cubierto (:func:`_archive_ts_covered_bytes`, MISMA función que al firmar)
    y se verifica el token RFC 3161 (misma TSA/pinning que XAdES-T) — RT-16,
    Pieza 6.3. Solo entonces ``VerifyOutcome.xades_level`` sube a
    ``"XAdES-LTA"``; con CertificateValues/RevocationValues válidos pero SIN
    ArchiveTimeStamp (o con uno que no valida), se reporta ``"XAdES-LT"``.

    ``VerifyOutcome.xades_level`` refleja SIEMPRE el nivel REALMENTE
    verificado para ESTE ``signed_xml`` (no el que se INTENTÓ al firmar) —
    retrocompatible: una firma XAdES-B/-T sin CertificateValues se reporta
    igual que antes de este incremento.

    (RAMA PERSONAL, E17/F4 Incremento 2, Baja #4) ``expected_user_ca_fingerprint``
    activa una rama SEPARADA de la LT institucional de arriba, para firmas
    ``xades_personal`` que embeben ``xades:CertificateValues`` con la sub-CA
    de usuarios (SIN ``RevocationValues``/``ArchiveTimeStamp`` — ver
    ``LocalSigner.sign``): valida basicConstraints/keyUsage/vigencia de esa
    sub-CA, la cadena leaf→sub-CA (``verify_directly_issued_by``) y el pin
    contra ``expected_user_ca_fingerprint`` (``firma.ca_fingerprint``,
    misma columna que el ancla LT institucional — nunca ambos parámetros se
    usan a la vez). Sigue SIN hacer I/O de red — la revocación en línea
    (``GET /internal/signing-key/revocation-status``) la resuelve el
    ROUTER, combinándola con este resultado. Una firma ``xades_personal`` de
    Incremento 1 (sin ``CertificateValues``) NUNCA llega a esta rama —
    retrocompat exacta, grandfather ESTRUCTURAL por FORMA del XML, no por un
    flag de versión.
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

    # (XAdES-T) Sello de tiempo, si lo hay — ver docstring "XAdES-T" arriba.
    # Se recalcula sobre el ds:SignatureValue TAL COMO llega en este
    # `signed_xml` (no el de la fila BD), así que una alteración posterior al
    # sellado se detecta aquí igual que la detectaría el propio TSTInfo.
    encapsulated_ts = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        "/xades:SignatureTimeStamp/xades:EncapsulatedTimeStamp",
        namespaces=_XADES_NS,
    )
    if encapsulated_ts is None or not (encapsulated_ts.text or "").strip():
        return VerifyOutcome(True, False, None, xades_level=SignatureFormat.XADES_B.value)

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
    if not ts_outcome.valido:
        return VerifyOutcome(True, False, None, sello_tiempo_valido=False)

    xades_level = SignatureFormat.XADES_T.value
    tsa_timestamp = ts_outcome.gen_time

    # (XAdES-LT/LTA) Solo se evalúa si la firma trae CertificateValues — ver
    # docstring de la función, sección "XAdES-LT/LTA". Ausente: retrocompat
    # exacta con el comportamiento previo a este incremento (se queda en T).
    cert_values_el = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        "/xades:CertificateValues/xades:EncapsulatedX509Certificate",
        namespaces=_XADES_NS,
    )
    if cert_values_el is None or not (cert_values_el.text or "").strip():
        return VerifyOutcome(
            True, False, None,
            sello_tiempo_valido=True, tsa_timestamp=tsa_timestamp, xades_level=xades_level,
        )

    try:
        embedded_ca_cert = x509.load_der_x509_certificate(b64decode(cert_values_el.text.strip()))
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return VerifyOutcome(False, False, f"ca_certificado_ilegible: {exc}")

    if expected_user_ca_fingerprint is not None:
        # --- RAMA PERSONAL (E17/F4, Incremento 2, Baja #4) — SEPARADA del
        # camino LT del sello institucional de abajo. Gobernada por
        # `expected_user_ca_fingerprint` presente (el router SOLO lo pasa
        # para filas `provider='xades_personal'`, ver
        # `app.routers.signature.verify`): una firma PERSONAL nunca cae en
        # el bloque institucional de más abajo (que INVALIDA sin
        # `RevocationValues` embebido — el "punto de máximo riesgo" de este
        # incremento). NUNCA exige `RevocationValues`/`ArchiveTimeStamp` —
        # la revocación de una clave personal se resuelve EN LÍNEA
        # (`GET /internal/signing-key/revocation-status`, llamada del
        # ROUTER, nunca de aquí — esta función sigue siendo PURA, sin I/O).
        # `xades_level` se queda en "XAdES-T": este material no representa
        # el paquete LT completo del sello, así que nunca se sobre-declara
        # "XAdES-LT" para una firma personal.
        sub_ca_fingerprint = embedded_ca_cert.fingerprint(hashes.SHA256()).hex()
        if sub_ca_fingerprint != expected_user_ca_fingerprint:
            return VerifyOutcome(False, False, "user_ca_fingerprint_no_coincide")
        try:
            sub_ca_bc = embedded_ca_cert.extensions.get_extension_for_class(
                x509.BasicConstraints
            )
        except x509.ExtensionNotFound:
            return VerifyOutcome(False, False, "user_ca_sin_basic_constraints")
        if not sub_ca_bc.value.ca:
            return VerifyOutcome(False, False, "user_ca_basic_constraints_no_es_ca")
        try:
            sub_ca_ku = embedded_ca_cert.extensions.get_extension_for_class(x509.KeyUsage)
        except x509.ExtensionNotFound:
            return VerifyOutcome(False, False, "user_ca_sin_key_usage")
        if not sub_ca_ku.value.key_cert_sign:
            return VerifyOutcome(False, False, "user_ca_sin_key_usage_cert_sign")
        if not (
            embedded_ca_cert.not_valid_before_utc <= now <= embedded_ca_cert.not_valid_after_utc
        ):
            return VerifyOutcome(False, False, "user_ca_certificado_fuera_de_vigencia")
        try:
            embedded_cert.verify_directly_issued_by(embedded_ca_cert)
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return VerifyOutcome(False, False, f"cadena_personal_ca_invalida: {exc}")
        return VerifyOutcome(
            True, False, None,
            sello_tiempo_valido=True, tsa_timestamp=tsa_timestamp, xades_level=xades_level,
        )

    # --- Camino institucional (sello del tenant) — SIN CAMBIOS ---
    ca_cert = embedded_ca_cert
    ca_fingerprint = ca_cert.fingerprint(hashes.SHA256()).hex()
    if not expected_ca_fingerprint:
        return VerifyOutcome(False, False, "ca_fingerprint_no_registrado")
    if ca_fingerprint != expected_ca_fingerprint:
        return VerifyOutcome(False, False, "ca_cert_fingerprint_no_coincide")

    try:
        ca_bc = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        return VerifyOutcome(False, False, "ca_sin_basic_constraints")
    if not ca_bc.value.ca:
        return VerifyOutcome(False, False, "ca_basic_constraints_no_es_ca")

    try:
        ca_ku = ca_cert.extensions.get_extension_for_class(x509.KeyUsage)
    except x509.ExtensionNotFound:
        return VerifyOutcome(False, False, "ca_sin_key_usage")
    if not ca_ku.value.key_cert_sign:
        return VerifyOutcome(False, False, "ca_sin_key_usage_cert_sign")

    if not (ca_cert.not_valid_before_utc <= now <= ca_cert.not_valid_after_utc):
        return VerifyOutcome(False, False, "ca_certificado_fuera_de_vigencia")

    # (c) Cadena seal -> CA: el certificado del sello debe haber sido
    # REALMENTE emitido por esta CA (firma + issuer/subject).
    try:
        embedded_cert.verify_directly_issued_by(ca_cert)
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return VerifyOutcome(False, False, f"cadena_seal_ca_invalida: {exc}")

    # (d) Revocación real: CRL firmada por la MISMA CA + serial no revocado.
    rev_values_el = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        "/xades:RevocationValues/xades:CRLValues/xades:EncapsulatedCRLValue",
        namespaces=_XADES_NS,
    )
    if rev_values_el is None or not (rev_values_el.text or "").strip():
        return VerifyOutcome(False, False, "revocation_values_ausente")

    try:
        crl = x509.load_der_x509_crl(b64decode(rev_values_el.text.strip()))
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return VerifyOutcome(False, False, f"crl_ilegible: {exc}")

    # `public_key()` incluye tipos de ACUERDO de claves (X25519, X448, DH,
    # ML-KEM) que no firman nada y que `is_signature_valid` no acepta. Se
    # excluyen aquí en vez de estrechar a RSA: la CA de este despliegue es RSA,
    # pero una CA con clave EC o Ed25519 es perfectamente válida y restringir a
    # RSA la rechazaría sin motivo. Fail-closed para lo que no puede firmar.
    ca_public_key = ca_cert.public_key()
    if not isinstance(
        ca_public_key,
        (
            rsa.RSAPublicKey,
            ec.EllipticCurvePublicKey,
            ed25519.Ed25519PublicKey,
            ed448.Ed448PublicKey,
            dsa.DSAPublicKey,
        ),
    ):
        return VerifyOutcome(False, False, "ca_clave_no_apta_para_firmar")
    if not crl.is_signature_valid(ca_public_key):
        return VerifyOutcome(False, False, "crl_firma_invalida")
    if crl.issuer != ca_cert.subject:
        return VerifyOutcome(False, False, "crl_issuer_no_coincide_ca")
    if crl.get_revoked_certificate_by_serial_number(embedded_cert.serial_number) is not None:
        return VerifyOutcome(False, False, "certificado_sello_revocado")

    # nextUpdate vencido se EXPONE, no invalida (perfil no_acreditado, CRL de
    # desarrollo — ver docstring de la función).
    crl_next_update = crl.next_update_utc

    # (e, OCSP stapled — RF-FIR-15) ADITIVO a la CRL ya validada arriba:
    # AUSENTE == sin cambios (retrocompat exacta, LT válida sobre CRL sola,
    # ver app.core.ocsp). PRESENTE-pero-inválido SÍ invalida la firma
    # completa (fail-closed) — mismo criterio que CertificateValues/
    # RevocationValues, a diferencia del XAdES-T "puro" ausente.
    ocsp_present = False
    ocsp_cert_status: str | None = None
    ocsp_values_el = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        "/xades:RevocationValues/xades:OCSPValues/xades:EncapsulatedOCSPValue",
        namespaces=_XADES_NS,
    )
    if ocsp_values_el is not None and (ocsp_values_el.text or "").strip():
        try:
            ocsp_response_der = b64decode(ocsp_values_el.text.strip())
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return VerifyOutcome(False, False, f"ocsp_ilegible: {exc}")

        ocsp_outcome = ocsp.verify_ocsp_response(
            ocsp_response_der,
            seal_cert=embedded_cert,
            ca_cert=ca_cert,
            expected_responder_fingerprint=expected_ocsp_responder_fingerprint,
        )
        if not ocsp_outcome.valido:
            return VerifyOutcome(False, False, f"ocsp_invalido: {ocsp_outcome.motivo}")
        ocsp_present = True
        ocsp_cert_status = ocsp_outcome.cert_status

    xades_level = SignatureFormat.XADES_LT.value

    # (ArchiveTimeStamp, XAdES-LTA) — Pieza 5/6.3.
    archive_ts_el = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        # SOLO el contenedor es v1.4.1 — su hijo EncapsulatedTimeStamp sigue
        # en v1.3.2 (ver docstring de _insert_archive_timestamp).
        "/xadesv141:ArchiveTimeStamp/xades:EncapsulatedTimeStamp",
        namespaces=_LT_NS,
    )
    archive_timestamp_present = bool(archive_ts_el is not None and (archive_ts_el.text or "").strip())
    if archive_timestamp_present:
        try:
            archive_token_der = b64decode(archive_ts_el.text.strip())
            covered = _archive_ts_covered_bytes(root)
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return VerifyOutcome(False, False, f"archive_timestamp_ilegible: {exc}")

        archive_outcome = tsa.verify_timestamp_token(
            archive_token_der, covered, expected_tsa_fingerprint=expected_tsa_fingerprint
        )
        if not archive_outcome.valido:
            return VerifyOutcome(
                False, False, f"archive_timestamp_invalido: {archive_outcome.motivo}"
            )
        xades_level = SignatureFormat.XADES_LTA.value

    return VerifyOutcome(
        True, False, None,
        sello_tiempo_valido=True,
        tsa_timestamp=tsa_timestamp,
        xades_level=xades_level,
        lt_material_present=True,
        archive_timestamp_present=archive_timestamp_present,
        # Procedencia SIEMPRE "dev" en este incremento — no hay integración
        # con una CA acreditada ONAC (RF-FIR-15, Pieza 7).
        revocation_provenance="dev",
        crl_next_update=crl_next_update,
        ocsp_present=ocsp_present,
        ocsp_cert_status=ocsp_cert_status,
    )
