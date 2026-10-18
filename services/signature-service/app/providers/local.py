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

--- Conformidad XMLDSig (ADR-034, B7) ---
``signxml`` (validado con 5.1.0) construye las referencias XAdES adicionales
(``SignedProperties``, ``KeyInfo``) SIN ``<ds:Transforms>``, pero calcula su
digest con el algoritmo de canonicalización del firmante (aquí, exclusiva).
XMLDSig §4.4.3.2 dice que una referencia sin ``Transforms`` se convierte a
octetos con C14N 1.0 INCLUSIVA: un verificador conforme (EU DSS) recalcula
otro digest y da ``HASH_FAILURE``. Eso NO era un bug de la librería sino
nuestro: declaramos algo distinto de lo que aplicábamos, y lo escondíamos al
verificar con un ``expect_config`` que le decía a nuestro propio verificador
«asume exclusiva» (el atajo, ya eliminado).

Corrección (:func:`_declarar_transforms` + :func:`_firmar_signed_info`): tras
``XAdESSigner.sign`` se declara explícitamente exc-c14n en cada referencia que
no lleve ``Transforms`` (los digests no cambian: ya eran exclusivos), y SOLO
ENTONCES se firma ``ds:SignedInfo`` —tanto el sello institucional como la
firma personal—, porque parchear cambia los octetos que se firman. La firma
resultante se autoverifica fail-closed con ``XAdESVerifier()`` en su
configuración por defecto (:func:`_autoverificar`) antes de sellar el tiempo o
persistir nada. :func:`verify_xades` verifica SOLO conforme al estándar: las
firmas anteriores a ADR-034 (referencias sin ``Transforms``) ya no validan.

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
     entrada de ETSI EN 319 132-1 §5.5.2 (octetos de cada referencia,
     SignedInfo, SignatureValue, KeyInfo y propiedades no firmadas previas; ver
     :func:`_archive_ts_covered_bytes` — build y verify comparten la MISMA
     función; cualquier divergencia rompe la verificación). Desde ADR-034 esa
     entrada la valida EU DSS (antes no era la de la norma).

LT y LTA se producen SIEMPRE juntos al firmar (nunca queda una firma "solo
LT" persistida) — la distinción entre ambos niveles solo emerge en
:func:`verify_xades` si el ``ArchiveTimeStamp`` no llega a validar pero el
resto del material LT sí. Sin material CA montado, la firma se queda en
XAdES-T: degradación OBSERVABLE (``firma.lt_downgrade_no_ca`` en
``app.routers.signature``), nunca silenciosa. Perfil "LTA SIMPLIFICADO, NO
ACREDITADO" (RF-FIR-15): con la CA de desarrollo ``acreditado`` queda en ``False``
(el nivel persistido es ``*_no_acreditado``; sin acreditación ONAC), y la propia TSA que sella el
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

import copy
import hashlib
import uuid
from base64 import b64decode, b64encode
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, padding, rsa
from cryptography.x509.oid import NameOID
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
_ENVELOPED_SIGNATURE_URI = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
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


class SelloInstitucionalVencidoError(RuntimeError):
    """El certificado del sello institucional está caducado (o aún no vigente): no se firma
    (ARC-M2). Fail-closed ANTES de producir nada; el router lo traduce a 503
    ``sello_institucional_vencido``. Antes se firmaba y solo ``/verify`` lo rechazaba, así que
    el sistema emitía y archivaba (WORM) firmas que nacían inverificables."""


class CertificadoPersonalVencidoError(RuntimeError):
    """El certificado de firma PERSONAL está caducado (o aún no vigente): no se firma
    (SEG-B3). Se lanza ANTES de ``remote_sign``, es decir, antes de que auth-service
    verifique y consuma el TOTP; el llamador lo traduce a 422 ``certificado_personal_vencido``."""


def _exigir_certificado_vigente(signer: SignerRef) -> None:
    """Vigencia del certificado del firmante AHORA, antes de cualquier acto de firma."""
    cert = x509.load_pem_x509_certificate(signer.cert_pem)
    ahora = datetime.now(UTC)
    if cert.not_valid_before_utc <= ahora <= cert.not_valid_after_utc:
        return
    estado = "caducó" if ahora > cert.not_valid_after_utc else "aún no es vigente"
    limite = (
        cert.not_valid_after_utc if ahora > cert.not_valid_after_utc else cert.not_valid_before_utc
    ).isoformat()
    if signer.remote_sign is not None:
        raise CertificadoPersonalVencidoError(
            f"su certificado de firma personal {estado} ({limite}); renuévelo antes de firmar"
        )
    raise SelloInstitucionalVencidoError(
        f"el certificado del sello institucional {estado} ({limite}); renueve el sello del tenant"
    )


class XmldsigNoConformeError(RuntimeError):
    """La firma recién producida no la verifica un verificador XMLDSig conforme
    (``XAdESVerifier()`` por defecto) — fail-closed (ADR-034): nunca se sella el
    tiempo ni se persiste una firma que un tercero no podría comprobar. El
    router lo traduce a 503."""


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

@dataclass(slots=True)
class VerifyOutcome:
    """Resultado de :func:`verify_xades` — nunca lanza para fallos de negocio."""

    valida: bool
    # (Sin `acreditado`: la acreditación es un hecho de la FIRMA —lo decidió el
    # sello/CA al firmar y quedó en `firma.nivel_conformidad`—, no algo que la
    # criptografía de verificación pueda afirmar. El router lo deriva de lo
    # PERSISTIDO y solo si la cripto valida; AREA-05-02.)
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
    # Cuántos ArchiveTimeStamp validó la cadena (1 = el de la firma original; >1 = renovada,
    # B10 §8) y el instante del último.
    archive_timestamp_count: int = 0
    archive_timestamp_ultimo_at: datetime | None = None
    # ¿El ÚLTIMO sello de archivo aún prueba? Su TSA tiene que seguir vigente HOY (EN 319 102-1
    # §5.6.2: sin un sello posterior, un último ATS con la TSA caducada no da POE). None si la
    # firma no es LTA. False degrada el nivel verificado a XAdES-LT.
    lta_vigente: bool | None = None
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


def _c14n_con_algoritmo(el: etree._Element, algorithm_uri: str) -> bytes:
    """Canonicaliza ``el`` con el algoritmo DECLARADO por URI (no uno fijo):
    quien firma debe aplicar exactamente lo que declara en el XML."""
    if algorithm_uri == CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0.value:
        return etree.tostring(el, method="c14n", exclusive=True)
    if algorithm_uri == CanonicalizationMethod.CANONICAL_XML_1_0.value:
        return etree.tostring(el, method="c14n", exclusive=False)
    raise ValueError(f"c14n_no_soportada: {algorithm_uri}")


def _declarar_transforms(signed_root: etree._Element) -> int:
    """Declara ``ds:Transforms`` exc-c14n en cada ``ds:Reference`` de
    ``ds:SignedInfo`` que no lo tenga (las de ``SignedProperties`` y
    ``KeyInfo`` que deja ``signxml``). Los digests NO cambian: ``signxml`` ya
    los calculó con ``_C14N_ALG``; aquí solo se declara lo que se aplicó, que
    es lo que un verificador conforme necesita (XMLDSig §4.4.3.2). Devuelve
    cuántas referencias se parchearon. Debe ejecutarse ANTES de firmar
    ``SignedInfo``."""
    parcheadas = 0
    for ref in signed_root.iterfind(".//ds:Signature/ds:SignedInfo/ds:Reference", namespaces=_DS_NS):
        if ref.find("ds:Transforms", namespaces=_DS_NS) is not None:
            continue
        transforms = etree.Element(f"{{{_DS_NS['ds']}}}Transforms")
        etree.SubElement(transforms, f"{{{_DS_NS['ds']}}}Transform").set(
            "Algorithm", _C14N_ALG.value
        )
        ref.insert(0, transforms)  # XMLDSig: Transforms precede a DigestMethod
        parcheadas += 1
    return parcheadas


def _c14n_declarado(sello_el: etree._Element, objetivo: etree._Element) -> bytes:
    """C14N de ``objetivo`` con el algoritmo que el propio sello de tiempo declara en su
    ``ds:CanonicalizationMethod`` (ARC-B2): quien verifica aplica lo que el XML declara, no
    un valor fijo. Sin método declarado o con uno no soportado: ``ValueError`` (fail-closed)."""
    metodo = sello_el.find("ds:CanonicalizationMethod", namespaces=_DS_NS)
    if metodo is None or not metodo.get("Algorithm"):
        raise ValueError("canonicalization_method_ausente_en_sello_de_tiempo")
    return _c14n_con_algoritmo(objetivo, metodo.get("Algorithm", ""))


def _signed_info_c14n(signed_root: etree._Element) -> bytes:
    """Octetos de ``ds:SignedInfo`` a firmar, con el algoritmo que ESE elemento
    declara en su ``CanonicalizationMethod`` (no un ``exclusive=True`` fijo)."""
    signed_info = signed_root.find(".//ds:Signature/ds:SignedInfo", namespaces=_DS_NS)
    if signed_info is None:  # pragma: no cover — signxml siempre produce SignedInfo
        raise ValueError("signed_info_no_encontrado")
    method = signed_info.find("ds:CanonicalizationMethod", namespaces=_DS_NS)
    if method is None:  # pragma: no cover
        raise ValueError("canonicalization_method_no_encontrado")
    return _c14n_con_algoritmo(signed_info, method.get("Algorithm", ""))


async def _firmar_signed_info(
    signed_root: etree._Element, signer: SignerRef
) -> etree._Element:
    """Firma ``ds:SignedInfo`` YA parcheado y escribe ``ds:SignatureValue``.

    Un solo camino para los dos firmantes: sello institucional (clave en
    proceso) y firma personal (``remote_sign`` sobre el SHA-256 de los octetos
    canónicos). El digest sale del ``SignedInfo`` FINAL, nunca de uno previo.
    Devuelve el elemento ``ds:SignatureValue``.
    """
    c14n = _signed_info_c14n(signed_root)
    if signer.remote_sign is not None:
        signature = await signer.remote_sign(hashlib.sha256(c14n).digest())
    else:
        key = serialization.load_pem_private_key(signer.key_pem, password=signer.key_password)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("clave_de_sello_no_rsa")
        signature = key.sign(c14n, padding.PKCS1v15(), hashes.SHA256())

    sig_value_el = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
    if sig_value_el is None:  # pragma: no cover — signxml siempre produce SignatureValue
        raise ValueError("signature_value_no_encontrado")
    sig_value_el.text = b64encode(signature).decode("ascii")
    return sig_value_el


def _autoverificar(signed_root: etree._Element, cert_pem: bytes) -> None:
    """Verifica la firma recién producida con ``XAdESVerifier()`` en su
    configuración por defecto —sin tocar la canonicalización por defecto, ni el
    instante de verificación, ni ningún atajo—: si eso falla, un tercero tampoco
    podrá verificarla. Fail-closed (ADR-034).

    La vigencia del certificado ya se exigió ANTES de firmar
    (:func:`_exigir_certificado_vigente`), así que no hace falta acotar
    ``verification_time``: un certificado caducado no llega hasta aquí.
    """
    cert = x509.load_pem_x509_certificate(cert_pem)
    try:
        XAdESVerifier().verify(signed_root, x509_cert=cert)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo == firma no conforme
        raise XmldsigNoConformeError(f"autoverificacion_xmldsig_fallida: {exc}") from exc


def _certificado_efimero(key: rsa.RSAPrivateKey) -> x509.Certificate:
    """Certificado autofirmado y desechable para el ensayo de XMLDSig (B10 §7)."""
    ahora = datetime.now(UTC)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ensayo-xmldsig")])
    return (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=5))
        .not_valid_after(ahora + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )


async def _ensayar_xmldsig(prepared: PreparedDoc) -> None:
    """Ensayo de la firma PERSONAL con un par y un certificado EFÍMEROS (B10 §7).

    Recorre la MISMA ruta que :meth:`LocalSigner.sign` (``XAdESSigner.sign`` ->
    :func:`_declarar_transforms` -> firma del ``SignedInfo`` FINAL -> :func:`_autoverificar`),
    pero con una clave que no es de nadie. Así la parte de la conformidad que depende del
    DOCUMENTO (c14n, Transforms, digests de las ``Reference``: la familia del ``HASH_FAILURE``) se
    comprueba ANTES de llamar a ``remote_sign``, es decir, antes de que auth-service consuma el
    TOTP. Lo que no cubre —la incrustación del ``SignatureValue`` REAL— es el residuo declarado
    en el ADR-034 y lo sigue cubriendo la autoverificación final.

    Los errores de parseo del documento propagan igual que en la ruta real.
    """
    root = _parse_no_doctype(prepared.xml_bytes)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = _certificado_efimero(key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    signed_root = XAdESSigner(
        signature_algorithm=_SIGN_ALG,
        digest_algorithm=_DIGEST_ALG,
        c14n_algorithm=_C14N_ALG,
    ).sign(root, key=key_pem, cert=cert_pem.decode("ascii"), always_add_key_value=False)
    del key_pem
    _declarar_transforms(signed_root)
    firma = key.sign(_signed_info_c14n(signed_root), padding.PKCS1v15(), hashes.SHA256())
    sig_value_el = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
    if sig_value_el is None:  # pragma: no cover — signxml siempre produce SignatureValue
        raise ValueError("signature_value_no_encontrado")
    sig_value_el.text = b64encode(firma).decode("ascii")
    _autoverificar(signed_root, cert_pem)


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


def _insert_certificate_values(unsigned_sig_props: etree._Element, *certs_der: bytes) -> None:
    """Incrusta ``xades:CertificateValues`` (XAdES-LT, Pieza 4).

    SOLO el certificado de la CA — el del sello institucional ya viaja en
    ``ds:KeyInfo/ds:X509Data/ds:X509Certificate`` y EN 319 132 §5.2.9 exige NO
    duplicar certificados ya presentes en ``KeyInfo``. ``unsigned_sig_props`` es también el
    padre válido en un ``xadesv141:TimeStampValidationData`` (renovación, B10 §8), donde se
    pasan los certificados de la TSA del sello anterior.
    """
    cert_values = etree.SubElement(unsigned_sig_props, f"{{{_XADES_NS_URI}}}CertificateValues")
    for ca_cert_der in certs_der:
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


def _octetos_de_referencia(ref: etree._Element, root: etree._Element) -> bytes:
    """Octetos que digiere una ``ds:Reference`` (XMLDSig §4.3.3.2/§4.4.3): la
    referida por su URI (``""`` = el documento SIN la firma que la contiene;
    ``#Id`` = ese elemento) tras aplicar sus ``ds:Transforms`` declarados; una
    referencia sin ``Transforms`` se canonicaliza con C14N 1.0 inclusiva.
    Solo las transformaciones que esta firma produce (enveloped-signature,
    exc-c14n, C14N 1.0); cualquier otra es ``ValueError`` (fail-closed)."""
    uri = ref.get("URI", "")
    if uri == "":
        objetivo = copy.deepcopy(root)
        firma_propia = ref.getparent().getparent()
        # La firma que contiene la referencia, localizada en la copia por posición.
        copia_firma = list(objetivo.iter())[list(root.iter()).index(firma_propia)]
        copia_firma.getparent().remove(copia_firma)
    elif uri.startswith("#"):
        encontrados = root.xpath("//*[@Id=$i]", i=uri[1:])
        if len(encontrados) != 1:
            raise ValueError(f"referencia_no_resuelve: {uri}")
        objetivo = encontrados[0]
    else:
        raise ValueError(f"uri_de_referencia_no_soportada: {uri}")

    algoritmo = CanonicalizationMethod.CANONICAL_XML_1_0.value
    for t in ref.findall("ds:Transforms/ds:Transform", namespaces=_DS_NS):
        alg = t.get("Algorithm", "")
        if alg == _ENVELOPED_SIGNATURE_URI:
            continue  # ya retirada arriba
        algoritmo = alg
    return _c14n_con_algoritmo(objetivo, algoritmo)


def _archive_ts_covered_bytes(
    signed_root: etree._Element,
    algoritmo: str = _C14N_ALG.value,
    *,
    objetivo: etree._Element | None = None,
) -> bytes:
    """Entrada del ``xadesv141:ArchiveTimeStamp`` según ETSI EN 319 132-1
    §5.5.2, concatenada en este orden:

      1. los octetos de CADA ``ds:Reference`` de ``SignedInfo``, en su orden
         (documento, ``SignedProperties``, ``KeyInfo``), tras sus Transforms
         (:func:`_octetos_de_referencia`);
      2. C14N(``ds:SignedInfo``) || C14N(``ds:SignatureValue``) ||
         C14N(``ds:KeyInfo``);
      3. C14N de cada propiedad no firmada de ``UnsignedSignatureProperties``
         que PRECEDE al ``ArchiveTimeStamp`` (en construcción, todas las
         presentes), en orden de documento: ``SignatureTimeStamp``,
         ``CertificateValues``, ``RevocationValues`` (con ``OCSPValues`` dentro) y, tras una
         renovación (B10 §8), los ``ArchiveTimeStamp`` y ``TimeStampValidationData`` anteriores.

    ``objetivo``: el ``ArchiveTimeStamp`` cuya entrada se calcula. Sus «previas» son TODOS los
    hermanos que lo anteceden (incluidos los ATS y los TSVD anteriores); así cada sello de una
    cadena de renovación se valida sobre lo que realmente tenía delante. Con ``None`` las
    previas son las que anteceden al PRIMER ``ArchiveTimeStamp`` (o todas, si aún no hay
    ninguno): el comportamiento de siempre para una firma de un solo sello.

    Antes de B7 la entrada omitía los octetos de las referencias y metía
    ``SignedProperties`` entre ``KeyInfo`` y los sellos: no era la de la norma
    y un validador independiente (EU DSS) daba ``MESSAGE_IMPRINT`` no íntegro.
    Esta forma la valida DSS (humo en ``tests/test_dss_smoke.py``).

    Debe llamarse ANTES de insertar el propio ``ArchiveTimeStamp``; build
    (``LocalSigner.sign``) y verify (:func:`verify_xades`) usan LA MISMA
    función.

    Raises:
        ValueError: falta un elemento requerido (firma incompleta o corrupta)
            o una referencia no se puede resolver.
    """
    signature = signed_root.find(".//ds:Signature", namespaces=_DS_NS)
    signed_info = signed_root.find(".//ds:Signature/ds:SignedInfo", namespaces=_DS_NS)
    signature_value = signed_root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
    key_info = signed_root.find(".//ds:Signature/ds:KeyInfo", namespaces=_DS_NS)
    qp = signed_root.find(".//xades:QualifyingProperties", namespaces=_XADES_NS)
    usp = (
        qp.find("xades:UnsignedProperties/xades:UnsignedSignatureProperties", namespaces=_XADES_NS)
        if qp is not None
        else None
    )

    required: dict[str, etree._Element | None] = {
        "ds:Signature": signature,
        "ds:SignedInfo": signed_info,
        "ds:SignatureValue": signature_value,
        "ds:KeyInfo": key_info,
        "xades:UnsignedSignatureProperties": usp,
    }
    missing = [name for name, el in required.items() if el is None]
    if missing:
        raise ValueError(f"elementos_faltantes_para_archive_timestamp: {missing}")
    assert signed_info is not None and usp is not None  # para el tipado

    previas: list[etree._Element] = []
    for hijo in usp:
        if objetivo is None:
            if hijo.tag == f"{{{_XADESV141_NS_URI}}}ArchiveTimeStamp":
                break
        elif hijo is objetivo:
            break
        previas.append(hijo)
    else:
        if objetivo is not None:
            raise ValueError("archive_timestamp_objetivo_no_esta_en_unsigned_signature_properties")
    presentes = {etree.QName(h).localname for h in previas}
    faltan = [
        n for n in ("SignatureTimeStamp", "CertificateValues", "RevocationValues") if n not in presentes
    ]
    if faltan:
        raise ValueError(f"elementos_faltantes_para_archive_timestamp: {faltan}")

    partes = [
        _octetos_de_referencia(ref, signed_root)
        for ref in signed_info.findall("ds:Reference", namespaces=_DS_NS)
    ]
    # `algoritmo` es el que declara el `ArchiveTimeStamp` (al firmar, el que se va a declarar).
    partes += [
        _c14n_con_algoritmo(el, algoritmo)
        for el in (signed_info, signature_value, key_info, *previas)
        if el is not None
    ]
    return b"".join(partes)


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
    ts_id = f"SignXMLArchiveTimeStamp{uuid.uuid4().hex[:8].upper()}"
    archive_ts = etree.SubElement(unsigned_sig_props, f"{{{_XADESV141_NS_URI}}}ArchiveTimeStamp")
    archive_ts.set("Id", ts_id)
    c14n_method = etree.SubElement(archive_ts, f"{{{_DS_NS['ds']}}}CanonicalizationMethod")
    c14n_method.set("Algorithm", _C14N_ALG.value)
    encapsulated_ts = etree.SubElement(archive_ts, f"{{{_XADES_NS_URI}}}EncapsulatedTimeStamp")
    # El elemento existe ANTES de calcular lo que cubre: sus «previas» son sus hermanos
    # anteriores (con renovaciones, también los ATS y TSVD anteriores).
    covered = _archive_ts_covered_bytes(signed_root, _C14N_ALG.value, objetivo=archive_ts)
    token = tsa.build_timestamp_token(covered)
    encapsulated_ts.text = b64encode(token.token_der).decode("ascii")
    return token


@dataclass(slots=True)
class ArchiveRenewal:
    """Resultado de :func:`renovar_archive_timestamp`."""

    signed_xml: bytes
    token: tsa.TimestampToken
    # Fin de vigencia del certificado de la TSA que firmó el sello NUEVO: la fecha antes de
    # la cual habrá que volver a renovar.
    tsa_cert_not_after: datetime
    # Ordinal del sello nuevo (0 = el de la firma original).
    ordinal: int


def renovar_archive_timestamp(signed_xml: bytes) -> ArchiveRenewal:
    """Añade un ``ArchiveTimeStamp`` nuevo a una firma XAdES-LTA (B10 §8; ETSI EN 319 132-1
    §5.5.2 y ``xadesv141:TimeStampValidationData``).

    Detrás del último ATS inserta un ``xadesv141:TimeStampValidationData`` con el certificado de
    la TSA del sello anterior (para que éste siga siendo verificable cuando su TSA ya no
    responda) y, a continuación, el ``ArchiveTimeStamp`` nuevo, cuya entrada incluye TODO lo
    anterior (los ATS y TSVD previos) además de las referencias, ``SignedInfo``,
    ``SignatureValue`` y ``KeyInfo``. NO verifica nada: el llamante verifica antes de renovar
    (renovar sellaría algo inválido).

    Raises:
        ValueError: la firma no lleva ``ArchiveTimeStamp`` (no es LTA) o está incompleta.
        app.core.tsa.TsaUnavailableError: ninguna TSA pudo sellar.
    """
    root = _parse_no_doctype(signed_xml)
    usp = root.find(
        ".//xades:QualifyingProperties/xades:UnsignedProperties/xades:UnsignedSignatureProperties",
        namespaces=_XADES_NS,
    )
    if usp is None:
        raise ValueError("sin_unsigned_signature_properties")
    anteriores = usp.findall("xadesv141:ArchiveTimeStamp", namespaces=_LT_NS)
    if not anteriores:
        raise ValueError("sin_archive_timestamp")
    ultimo_ts = anteriores[-1].find("xades:EncapsulatedTimeStamp", namespaces=_XADES_NS)
    if ultimo_ts is None or not (ultimo_ts.text or "").strip():
        raise ValueError("archive_timestamp_vacio")
    token_previo = b64decode(ultimo_ts.text.strip())
    certs_tsa = tsa.certificados_de_token(token_previo)
    # EN 319 132-1 §5.5.1: el TSVD lleva los datos de validación del sello anterior —certificados
    # Y revocación—. Se resuelve ANTES de tocar el árbol: si no se puede, no se renueva.
    cert_tsa_previo = tsa.certificado_tsa_de_token(token_previo)
    crl_tsa = tsa.revocacion_de_tsa(cert_tsa_previo, certs_tsa) if cert_tsa_previo is not None else None

    tsvd = etree.SubElement(usp, f"{{{_XADESV141_NS_URI}}}TimeStampValidationData")
    tsvd.set("Id", f"SignXMLTsvd{uuid.uuid4().hex[:8].upper()}")
    if certs_tsa:
        _insert_certificate_values(tsvd, *[c.public_bytes(serialization.Encoding.DER) for c in certs_tsa])
    if crl_tsa is not None:
        _insert_revocation_values(tsvd, crl_tsa)
    token = _insert_archive_timestamp(root, usp)
    tsa_cert = tsa.certificado_tsa_de_token(token.token_der)
    if tsa_cert is None:
        raise tsa.TsaUnavailableError("tsa_token_sin_certificado_firmante")
    return ArchiveRenewal(
        signed_xml=etree.tostring(root, xml_declaration=False, encoding="UTF-8"),
        token=token,
        tsa_cert_not_after=tsa_cert.not_valid_after_utc,
        ordinal=len(anteriores),
    )


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
        # Fail-closed ANTES de firmar (ARC-M2/SEG-B3): certificado caducado => ni firma, ni
        # llamada a `remote_sign` (que consumiría el TOTP), ni archivo de algo inverificable.
        _exigir_certificado_vigente(signer)

        # B10 §7: el ensayo de XMLDSig va ANTES de `remote_sign` (que consume el TOTP en
        # auth-service). Un documento que no firmaría conforme falla aquí, con el factor intacto.
        if signer.remote_sign is not None:
            await _ensayar_xmldsig(prepared)

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

        else:
            signed_root = xades_signer.sign(
                root,
                key=signer.key_pem,
                passphrase=signer.key_password,
                cert=signer.cert_pem.decode("ascii"),  # ver nota arriba
            )

        # Común a sello y firma personal (ADR-034): declarar los Transforms que
        # signxml omite, firmar el SignedInfo FINAL y autoverificar conforme.
        _declarar_transforms(signed_root)
        sig_value_el = await _firmar_signed_info(signed_root, signer)
        signature_value_b64 = sig_value_el.text or ""
        _autoverificar(signed_root, signer.cert_pem)

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
                # Lo decide `tsa.sello_acreditado()`, no un literal aquí: la TSA
                # LOCAL nunca se acredita (par autofirmado del despliegue), pero
                # una REMOTA contratada por la entidad sí puede estarlo, y era
                # un `False` fijo el que impedía declararlo (RF-FIR-15 prohíbe
                # sobre-declarar, no obliga a sub-declarar siempre).
                "tsa_acreditado": tsa.sello_acreditado(),
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
                    # El sufijo lo decide el SELLO, no este bloque. Antes era el
                    # literal "xades_lta_no_acreditado", y eso pisaba lo que
                    # `load_seal` hubiera resuelto: una entidad con material de
                    # un operador acreditado ONAC veía su firma degradada a
                    # "no acreditado" en cuanto añadía material LT/LTA — es
                    # decir, precisamente cuando la firma es MÁS fuerte.
                    # Los dos valores están admitidos por el CHECK desde la
                    # migración 017, y `tests/test_nivel_conformidad_admitido.py`
                    # vigila que sigan estándolo.
                    nivel_conformidad = (
                        "xades_lta_acreditado" if signer.acreditado else "xades_lta_no_acreditado"
                    )
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
    advertencias: list[str] | None = None,
    _vigencia_diferida: bool = False,
    tsa_fingerprints_archivo: Sequence[str] = (),
) -> VerifyOutcome:
    """Verifica una firma XAdES-B/-T/-LT/-LTA enveloped (E06, RF-FIR-15).

    VIGENCIA DEL CERTIFICADO (ARC-M1, ADR-034). Por regla, el certificado firmante se
    juzga a «ahora». Única excepción: si ya CADUCÓ y la firma lleva un
    ``ArchiveTimeStamp`` que valida (nivel verificado ``XAdES-LTA``), se juzga a la fecha
    ``genTime`` del ``SignatureTimeStamp`` —ya validado contra la vigencia del certificado
    en ``tsa.verify_timestamp_token``—, y la respuesta lo avisa en ``advertencias``. Sin un
    LTA válido (B, T, LT, o un ``ArchiveTimeStamp`` que no valida) se mantiene
    ``certificado_fuera_de_vigencia``. ``_vigencia_diferida`` es el modo interno de esa
    segunda pasada; los llamadores no lo usan. NO hay renovación del ``ArchiveTimeStamp``:
    la prueba descansa en el primero (deuda declarada).

    ``advertencias``: lista del LLAMADOR a la que se añaden los avisos de una
    verificación que sigue siendo válida (hoy: sello de tiempo sin ESSCertID
    anterior a ``settings.tsa_esscertid_corte``). Es un parámetro de salida y
    no un campo de ``VerifyOutcome`` porque la función tiene 40 retornos y un
    aviso no cambia el veredicto: ninguno de ellos tiene que acordarse de él.

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

    ``VerifyOutcome`` ya NO lleva ``acreditado`` (AREA-05-02): verificar la
    criptografía no puede afirmar acreditación. Es un hecho del momento de
    FIRMAR (el sello/CA que se usó, ``app.core.keys._leer_acreditacion``) y
    quedó persistido en ``firma.nivel_conformidad``; el router lo deriva de ahí
    y solo si esta función da ``valida=True``. Con la CA local de desarrollo
    (sin acreditación ONAC) ese nivel es ``*_no_acreditado`` y la respuesta es
    ``acreditado=False`` (RF-FIR-15 — ver la nota de módulo "XAdES-LT/LTA").

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
        return VerifyOutcome(False, f"xml_invalido: {exc}")
    except ValueError:
        return VerifyOutcome(False, "dtd_no_permitido")

    cert_el = root.find(
        ".//ds:Signature/ds:KeyInfo/ds:X509Data/ds:X509Certificate", namespaces=_DS_NS
    )
    if cert_el is None or not cert_el.text:
        return VerifyOutcome(False, "sin_certificado_embebido")

    try:
        embedded_cert = x509.load_der_x509_certificate(b64decode(cert_el.text.strip()))
    except Exception as exc:  # noqa: BLE001 — certificado corrupto, fail-closed
        return VerifyOutcome(False, f"certificado_ilegible: {exc}")

    now = datetime.now(UTC)
    if not _vigencia_diferida and not (
        embedded_cert.not_valid_before_utc <= now <= embedded_cert.not_valid_after_utc
    ):
        # Caducado (no «aún no vigente») y con ArchiveTimeStamp: segunda pasada, que solo
        # lo acepta si el nivel verificado es XAdES-LTA. El resto, fuera de vigencia.
        if embedded_cert.not_valid_after_utc < now and root.find(
            ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
            "/xadesv141:ArchiveTimeStamp",
            namespaces=_LT_NS,
        ) is not None:
            avisos: list[str] = []
            segunda = verify_xades(
                signed_xml,
                expected_fingerprint,
                expected_tsa_fingerprint,
                expected_ca_fingerprint,
                expected_ocsp_responder_fingerprint,
                expected_user_ca_fingerprint,
                avisos,
                _vigencia_diferida=True,
                tsa_fingerprints_archivo=tsa_fingerprints_archivo,
            )
            if segunda.valida and segunda.xades_level == SignatureFormat.XADES_LTA.value:
                if advertencias is not None:
                    for a in (*avisos, "certificado_vencido_vigencia_juzgada_al_sello_de_firma"):
                        if a not in advertencias:
                            advertencias.append(a)
                return segunda
        return VerifyOutcome(False, "certificado_fuera_de_vigencia")

    embedded_fingerprint = embedded_cert.fingerprint(hashes.SHA256()).hex()
    if embedded_fingerprint != expected_fingerprint:
        return VerifyOutcome(False, "fingerprint_no_coincide_sello_tenant")

    try:
        # Configuración POR DEFECTO, sin atajos: lo que no verifique así tampoco
        # lo verificará un tercero (ADR-034).
        if _vigencia_diferida:
            # Solo para que signxml no rechace por tiempo la comprobación CRIPTOGRÁFICA de
            # una firma con certificado caducado; la vigencia a la fecha de la firma la
            # decide el `SignatureTimeStamp` (signer_cert) más abajo, no este instante.
            results = XAdESVerifier().verify(
                root,
                x509_cert=embedded_cert,
                expect_config=XAdESSignatureConfiguration(
                    verification_time=embedded_cert.not_valid_after_utc
                ),
            )
        else:
            results = XAdESVerifier().verify(root, x509_cert=embedded_cert)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo == firma inválida (fail-closed)
        return VerifyOutcome(False, f"firma_invalida: {exc}")

    # (2) La referencia principal debe cubrir la raíz completa del documento.
    covers_root = any(
        r.signed_xml is not None and r.signed_xml.tag == root.tag for r in results
    )
    if not covers_root:
        return VerifyOutcome(False, "referencia_no_cubre_raiz")

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
        return VerifyOutcome(True, None, xades_level=SignatureFormat.XADES_B.value)

    sig_value_el = root.find(".//ds:Signature/ds:SignatureValue", namespaces=_DS_NS)
    if sig_value_el is None:  # pragma: no cover — imposible si la firma XAdES-B ya validó
        return VerifyOutcome(True, None, sello_tiempo_valido=False)

    try:
        token_der = b64decode(encapsulated_ts.text.strip())
        # Lo que el sello DECLARA (ARC-B2), no un exc-c14n fijo.
        sig_value_c14n = _c14n_declarado(encapsulated_ts.getparent(), sig_value_el)
    except Exception:  # noqa: BLE001 — base64 corrupto o c14n no declarada/soportada, fail-closed
        return VerifyOutcome(True, None, sello_tiempo_valido=False)

    ts_outcome = tsa.verify_timestamp_token(
        token_der,
        sig_value_c14n,
        expected_tsa_fingerprint=expected_tsa_fingerprint,
        # El genTime del SignatureTimeStamp tiene que caer en la vigencia del
        # certificado firmante (AREA-04-08). El ArchiveTimeStamp de más abajo NO.
        signer_cert=embedded_cert,
    )
    if not ts_outcome.valido:
        return VerifyOutcome(True, None, sello_tiempo_valido=False)
    if advertencias is not None:
        advertencias.extend(a for a in ts_outcome.advertencias if a not in advertencias)

    xades_level = SignatureFormat.XADES_T.value
    tsa_timestamp = ts_outcome.gen_time
    # Instante con el que se juzgan las CA: «ahora», salvo en la segunda pasada de una
    # firma LTA con certificado caducado, donde es la fecha del SignatureTimeStamp.
    instante_ca = tsa_timestamp if _vigencia_diferida and tsa_timestamp is not None else now

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
            True, None,
            sello_tiempo_valido=True, tsa_timestamp=tsa_timestamp, xades_level=xades_level,
        )

    try:
        embedded_ca_cert = x509.load_der_x509_certificate(b64decode(cert_values_el.text.strip()))
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return VerifyOutcome(False, f"ca_certificado_ilegible: {exc}")

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
            return VerifyOutcome(False, "user_ca_fingerprint_no_coincide")
        try:
            sub_ca_bc = embedded_ca_cert.extensions.get_extension_for_class(
                x509.BasicConstraints
            )
        except x509.ExtensionNotFound:
            return VerifyOutcome(False, "user_ca_sin_basic_constraints")
        if not sub_ca_bc.value.ca:
            return VerifyOutcome(False, "user_ca_basic_constraints_no_es_ca")
        try:
            sub_ca_ku = embedded_ca_cert.extensions.get_extension_for_class(x509.KeyUsage)
        except x509.ExtensionNotFound:
            return VerifyOutcome(False, "user_ca_sin_key_usage")
        if not sub_ca_ku.value.key_cert_sign:
            return VerifyOutcome(False, "user_ca_sin_key_usage_cert_sign")
        if not (
            embedded_ca_cert.not_valid_before_utc <= instante_ca <= embedded_ca_cert.not_valid_after_utc
        ):
            return VerifyOutcome(False, "user_ca_certificado_fuera_de_vigencia")
        try:
            embedded_cert.verify_directly_issued_by(embedded_ca_cert)
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return VerifyOutcome(False, f"cadena_personal_ca_invalida: {exc}")
        return VerifyOutcome(
            True, None,
            sello_tiempo_valido=True, tsa_timestamp=tsa_timestamp, xades_level=xades_level,
        )

    # --- Camino institucional (sello del tenant) — SIN CAMBIOS ---
    ca_cert = embedded_ca_cert
    ca_fingerprint = ca_cert.fingerprint(hashes.SHA256()).hex()
    if not expected_ca_fingerprint:
        return VerifyOutcome(False, "ca_fingerprint_no_registrado")
    if ca_fingerprint != expected_ca_fingerprint:
        return VerifyOutcome(False, "ca_cert_fingerprint_no_coincide")

    try:
        ca_bc = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        return VerifyOutcome(False, "ca_sin_basic_constraints")
    if not ca_bc.value.ca:
        return VerifyOutcome(False, "ca_basic_constraints_no_es_ca")

    try:
        ca_ku = ca_cert.extensions.get_extension_for_class(x509.KeyUsage)
    except x509.ExtensionNotFound:
        return VerifyOutcome(False, "ca_sin_key_usage")
    if not ca_ku.value.key_cert_sign:
        return VerifyOutcome(False, "ca_sin_key_usage_cert_sign")

    if not (ca_cert.not_valid_before_utc <= instante_ca <= ca_cert.not_valid_after_utc):
        return VerifyOutcome(False, "ca_certificado_fuera_de_vigencia")

    # (c) Cadena seal -> CA: el certificado del sello debe haber sido
    # REALMENTE emitido por esta CA (firma + issuer/subject).
    try:
        embedded_cert.verify_directly_issued_by(ca_cert)
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return VerifyOutcome(False, f"cadena_seal_ca_invalida: {exc}")

    # (d) Revocación real: CRL firmada por la MISMA CA + serial no revocado.
    rev_values_el = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        "/xades:RevocationValues/xades:CRLValues/xades:EncapsulatedCRLValue",
        namespaces=_XADES_NS,
    )
    if rev_values_el is None or not (rev_values_el.text or "").strip():
        return VerifyOutcome(False, "revocation_values_ausente")

    try:
        crl = x509.load_der_x509_crl(b64decode(rev_values_el.text.strip()))
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return VerifyOutcome(False, f"crl_ilegible: {exc}")

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
        return VerifyOutcome(False, "ca_clave_no_apta_para_firmar")
    if not crl.is_signature_valid(ca_public_key):
        return VerifyOutcome(False, "crl_firma_invalida")
    if crl.issuer != ca_cert.subject:
        return VerifyOutcome(False, "crl_issuer_no_coincide_ca")
    if crl.get_revoked_certificate_by_serial_number(embedded_cert.serial_number) is not None:
        return VerifyOutcome(False, "certificado_sello_revocado")

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
            return VerifyOutcome(False, f"ocsp_ilegible: {exc}")

        ocsp_outcome = ocsp.verify_ocsp_response(
            ocsp_response_der,
            seal_cert=embedded_cert,
            ca_cert=ca_cert,
            expected_responder_fingerprint=expected_ocsp_responder_fingerprint,
        )
        if not ocsp_outcome.valido:
            return VerifyOutcome(False, f"ocsp_invalido: {ocsp_outcome.motivo}")
        ocsp_present = True
        ocsp_cert_status = ocsp_outcome.cert_status

    xades_level = SignatureFormat.XADES_LT.value

    # (ArchiveTimeStamp, XAdES-LTA) — Pieza 5/6.3, y su cadena de renovación (B10 §8).
    # Cada ArchiveTimeStamp se valida sobre SUS propios hermanos previos (incluidos los ATS y los
    # TimeStampValidationData anteriores). El primero se ancla a la TSA de la firma; los
    # renovados, a la TSA de la firma o a una de las que quedaron registradas al renovar
    # (`tsa_fingerprints_archivo`, de la tabla `firma_sello_archivo`): una cadena de sellos
    # cuyas TSA no estén ancladas no prueba nada. Y cada sello tiene que haberse renovado ANTES
    # de que caducara el certificado de su TSA: es lo que hace de la cadena una prueba continua.
    archive_els = root.findall(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties"
        "/xadesv141:ArchiveTimeStamp",
        namespaces=_LT_NS,
    )
    archive_timestamp_present = False
    archive_count = 0
    archive_ultimo_at: datetime | None = None
    prev_at: datetime | None = None
    prev_not_after: datetime | None = None
    if archive_els:
        for i, ats_el in enumerate(archive_els):
            enc = ats_el.find("xades:EncapsulatedTimeStamp", namespaces=_XADES_NS)
            if enc is None or not (enc.text or "").strip():
                if i == 0:
                    break  # un primer ATS vacío == sin ATS (como siempre)
                return VerifyOutcome(False, f"archive_timestamp_vacio[{i}]")
            try:
                archive_token_der = b64decode(enc.text.strip())
                archive_metodo = ats_el.find("ds:CanonicalizationMethod", namespaces=_DS_NS)
                if archive_metodo is None or not archive_metodo.get("Algorithm"):
                    raise ValueError("canonicalization_method_ausente_en_archive_timestamp")
                covered = _archive_ts_covered_bytes(
                    root, archive_metodo.get("Algorithm", ""), objetivo=ats_el
                )
                # El pin de esta pieza: el de la firma para el primero; para los renovados, el
                # de la firma o uno registrado al renovar.
                pin = expected_tsa_fingerprint
                if i > 0:
                    huella_token = tsa.certificados_de_token(archive_token_der)[0].fingerprint(
                        hashes.SHA256()
                    ).hex()
                    permitidas = {expected_tsa_fingerprint, *tsa_fingerprints_archivo} - {None}
                    if huella_token not in permitidas:
                        return VerifyOutcome(False, f"archive_timestamp_tsa_no_anclada[{i}]")
                    pin = huella_token
            except Exception as exc:  # noqa: BLE001 — fail-closed
                return VerifyOutcome(False, f"archive_timestamp_ilegible: {exc}")

            archive_outcome = tsa.verify_timestamp_token(
                archive_token_der, covered, expected_tsa_fingerprint=pin
            )
            if not archive_outcome.valido:
                return VerifyOutcome(
                    False, f"archive_timestamp_invalido[{i}]: {archive_outcome.motivo}"
                )
            if (
                prev_at is not None
                and archive_outcome.gen_time is not None
                and archive_outcome.gen_time < prev_at
            ):
                return VerifyOutcome(False, f"archive_timestamp_cadena_desordenada[{i}]")
            if (
                prev_not_after is not None
                and archive_outcome.gen_time is not None
                and archive_outcome.gen_time > prev_not_after
            ):
                # El sello anterior se renovó cuando el certificado de SU TSA ya había caducado:
                # entre medias no hubo prueba vigente.
                return VerifyOutcome(False, f"archive_timestamp_cadena_rota[{i}]")
            prev_at = archive_outcome.gen_time
            prev_not_after = archive_outcome.tsa_cert_not_after
            archive_ultimo_at = archive_outcome.gen_time
            archive_count += 1
            archive_timestamp_present = True
            if advertencias is not None:
                advertencias.extend(a for a in archive_outcome.advertencias if a not in advertencias)
        if archive_timestamp_present:
            xades_level = SignatureFormat.XADES_LTA.value

    lta_vigente: bool | None = None
    if archive_timestamp_present:
        lta_vigente = prev_not_after is None or prev_not_after >= now
        if not lta_vigente:
            # El último sello se juzga a la fecha ACTUAL: su TSA caducó y nadie lo renovó, así que
            # la cadena ya no prueba nada. Se declara LT (no LTA) y, como el nivel LTA es lo que
            # habilita juzgar un firmante caducado a la fecha de su sello (`_vigencia_diferida`),
            # esa excepción tampoco se aplica.
            xades_level = SignatureFormat.XADES_LT.value
            if advertencias is not None and "archive_timestamp_caducado" not in advertencias:
                advertencias.append("archive_timestamp_caducado")

    return VerifyOutcome(
        True, None,
        sello_tiempo_valido=True,
        tsa_timestamp=tsa_timestamp,
        xades_level=xades_level,
        lt_material_present=True,
        archive_timestamp_present=archive_timestamp_present,
        archive_timestamp_count=archive_count,
        archive_timestamp_ultimo_at=archive_ultimo_at,
        lta_vigente=lta_vigente,
        # Procedencia SIEMPRE "dev" en este incremento — no hay integración
        # con una CA acreditada ONAC (RF-FIR-15, Pieza 7).
        revocation_provenance="dev",
        crl_next_update=crl_next_update,
        ocsp_present=ocsp_present,
        ocsp_cert_status=ocsp_cert_status,
    )
