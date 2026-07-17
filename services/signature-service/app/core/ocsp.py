# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""OCSP (RFC 6960) stapled en XAdES-LT — E06, RF-FIR-15.

Construye y verifica una ``BasicOCSPResponse`` DER que
:mod:`app.providers.local` incrusta como
``xades:RevocationValues/xades:OCSPValues/xades:EncapsulatedOCSPValue`` —
material de revocación ADICIONAL a la CRL ya presente (``xades:CRLValues``),
NUNCA en su lugar: XAdES-LT sigue exigiendo la CRL (:func:`app.providers.local
._insert_revocation_values`); OCSP es aditivo y opt-in
(``settings.signer_ocsp_enabled=False`` por defecto).

--- Modelo de confianza: responder DELEGADO, coherente con la CRL ---
Este módulo NUNCA habla HTTP con un responder OCSP externo — el "responder"
es un par de claves propio (``ocsp.crt``/``ocsp.key``) emitido OFFLINE por la
misma CA local de desarrollo que emite los sellos institucionales
(``ca.key`` — ``scripts/gen-dev-ca.sh``/``scripts/gen-dev-ocsp-responder.sh``;
la clave de la CA NUNCA entra a este servicio, ver ``app.core.keys``). El
``cert_status`` de la respuesta se DERIVA de la MISMA CRL que ya usa XAdES-LT
(:func:`build_ocsp_response`) — no hay dos fuentes de verdad de revocación
que puedan divergir dentro de un mismo acto de firma.

--- Por qué SHA-1 en el CertID (RFC 6960 §4.1.1) ---
El algoritmo de ``CertID`` (``issuerNameHash``/``issuerKeyHash``) es SHA-1 por
ser el valor por defecto histórico del protocolo OCSP y el que la mayoría de
verificadores esperan — esto es SOLO identificación del certificado
consultado dentro del CertID, NO la integridad de la respuesta: la
``BasicOCSPResponse`` completa se firma con RSA-SHA256
(:func:`build_ocsp_response`, ``.sign(responder_key, hashes.SHA256())``), que
es lo que :func:`verify_ocsp_response` valida criptográficamente.

--- Responder DELEGADO: EKU + id-pkix-ocsp-nocheck (RFC 6960 §4.2.2.2) ---
El certificado del responder lleva EKU ``id-kp-OCSPSigning``
(1.3.6.1.5.5.7.3.9) — sin este EKU, un certificado emitido por la CA para
CUALQUIER otro propósito podría reutilizarse para firmar respuestas OCSP.
También lleva la extensión ``id-pkix-ocsp-nocheck`` (1.3.6.1.5.5.7.48.1.5,
valor NULL) — declara que el propio certificado del responder NO necesita
comprobación de revocación (evita la paradoja circular de tener que consultar
OCSP para validar el certificado que firma las respuestas OCSP).
:func:`verify_ocsp_response` exige AMBAS extensiones, fail-closed.

--- Pinning del responder (mismo patrón que TSA/CA — ``app.core.tsa``/
``app.providers.local``) ---
``xades:OCSPValues`` es, igual que ``xades:CertificateValues``/
``RevocationValues``, una propiedad NO FIRMADA (fuera de ``ds:SignedInfo``):
sin cotejar el certificado del responder embebido contra
``firma.ocsp_responder_fingerprint`` persistido al firmar, cualquiera con un
XML XAdES-LT genuino del tenant podría anexarle una respuesta OCSP firmada
con SU PROPIO responder (emitido por SU propia CA, con EKU OCSPSigning
válido) declarando el estado que quisiera. ``expected_responder_fingerprint
=None`` (sin columna poblada) invalida — fail-closed, nunca se omite.

--- Conformidad (RF-FIR-15) ---
El responder de este incremento es delegado, emitido por una CA de
desarrollo sin acreditación ONAC — igual que la CRL/TSA/CA del resto de
XAdES-LT/LTA, nunca sube ``acreditado``/``xades_level`` por sí solo (ver
:mod:`app.providers.local`, sección "XAdES-LT/LTA").

--- Nonce (RFC 8954) N/A en este modelo ---
El nonce de OCSP (RFC 8954) liga una respuesta a SU request específica en el
protocolo OCSP EN LÍNEA (cliente pregunta, responder contesta con el mismo
nonce) — aquí no aplica: el modelo es STAPLED, sin request en tiempo real
(:func:`build_ocsp_response` se invoca al firmar, no en respuesta a una
consulta), así que no hay nonce que cotejar. Su ausencia no oculta una
debilidad operativa; la ligazón respuesta<->sello la da el CertID (comprobación
6 de :func:`verify_ocsp_response`), no el nonce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.ocsp import (
    OCSPCertStatus,
    OCSPRequestBuilder,
    OCSPResponderEncoding,
    OCSPResponseBuilder,
    OCSPResponseStatus,
    load_der_ocsp_response,
)
from cryptography.x509.oid import ExtendedKeyUsageOID

from app.core.config import settings


class OcspMaterialRequiredError(RuntimeError):
    """``settings.signer_ocsp_require=True`` pero el tenant no tiene material
    de responder OCSP (``ocsp.crt``/``ocsp.key``) montado junto a su sello
    institucional — fail-closed, el router lo traduce a 503 (análogo a
    ``app.providers.local.LtMaterialRequiredError``).
    """


@dataclass(slots=True)
class OcspResponse:
    """Respuesta OCSP producida, lista para incrustar en el XML firmado."""

    response_der: bytes
    produced_at: datetime
    this_update: datetime
    next_update: datetime | None
    cert_status: str  # "good" | "revoked"
    responder_cert_fingerprint: str


@dataclass(slots=True)
class OcspVerifyOutcome:
    """Resultado de :func:`verify_ocsp_response` — nunca lanza (fail-closed)."""

    valido: bool
    cert_status: str | None = None
    produced_at: datetime | None = None
    motivo: str | None = None


def build_ocsp_response(
    seal_cert: x509.Certificate,
    ca_cert: x509.Certificate,
    responder_cert: x509.Certificate,
    responder_key: rsa.RSAPrivateKey,
    *,
    crl: x509.CertificateRevocationList,
) -> OcspResponse:
    """Construye una ``BasicOCSPResponse`` DELEGADA (RFC 6960) sobre
    ``seal_cert``, firmada por ``responder_key``/``responder_cert``.

    ``cert_status`` se DERIVA de ``crl`` (la MISMA CRL ya embebida como
    ``xades:CRLValues`` por XAdES-LT, ver
    ``app.providers.local._insert_revocation_values``): si el serial de
    ``seal_cert`` NO aparece en ``crl``, ``GOOD`` (``revocation_time``/
    ``reason=None``); si aparece, ``REVOKED`` con el ``revocation_time`` del
    entry — nunca dos fuentes de verdad de revocación que puedan divergir
    dentro del mismo acto de firma (coherencia CRL<->OCSP).

    El CertID usa ``algorithm=hashes.SHA1()`` (RFC 6960 §4.1.1 — SOLO
    identificación del certificado consultado dentro del CertID, NO
    integridad de la respuesta; la ``BasicOCSPResponse`` se firma con
    RSA-SHA256). ``this_update=now`` (UTC), ``next_update=now +
    settings.signer_ocsp_validity_days``. El responder se declara vía
    ``responder_id(HASH, responder_cert)`` y se embebe con
    ``.certificates([responder_cert])`` — así :func:`verify_ocsp_response`
    puede leerlo de la propia respuesta sin material adicional.
    """
    entry = crl.get_revoked_certificate_by_serial_number(seal_cert.serial_number)
    if entry is None:
        cert_status = OCSPCertStatus.GOOD
        revocation_time = None
        revocation_reason = None
    else:
        cert_status = OCSPCertStatus.REVOKED
        revocation_time = entry.revocation_date_utc
        try:
            revocation_reason = entry.extensions.get_extension_for_class(x509.CRLReason).value.reason
        except x509.ExtensionNotFound:
            revocation_reason = None

    now = datetime.now(UTC)
    next_update = now + timedelta(days=settings.signer_ocsp_validity_days)

    builder = (
        OCSPResponseBuilder()
        .add_response(
            cert=seal_cert,
            issuer=ca_cert,
            algorithm=hashes.SHA1(),
            cert_status=cert_status,
            this_update=now,
            next_update=next_update,
            revocation_time=revocation_time,
            revocation_reason=revocation_reason,
        )
        .responder_id(OCSPResponderEncoding.HASH, responder_cert)
        .certificates([responder_cert])
    )
    response = builder.sign(responder_key, hashes.SHA256())
    response_der = response.public_bytes(serialization.Encoding.DER)

    return OcspResponse(
        response_der=response_der,
        produced_at=response.produced_at_utc,
        this_update=now,
        next_update=next_update,
        cert_status="good" if entry is None else "revoked",
        responder_cert_fingerprint=responder_cert.fingerprint(hashes.SHA256()).hex(),
    )


def verify_ocsp_response(
    response_der: bytes,
    *,
    seal_cert: x509.Certificate,
    ca_cert: x509.Certificate,
    expected_responder_fingerprint: str | None,
) -> OcspVerifyOutcome:
    """Verifica una ``BasicOCSPResponse`` (RFC 6960) — fail-closed, nunca
    lanza (mismo contrato que ``app.core.tsa.verify_timestamp_token``).

    Comprueba, EN ORDEN (cualquier fallo detiene la cadena):

      1. ``response_status == SUCCESSFUL``.
      2. Hay al menos un certificado embebido (el responder).
      3. El responder fue REALMENTE emitido por ``ca_cert``
         (``verify_directly_issued_by``) — cadena responder->CA.
      4. Ventana de vigencia del certificado del RESPONDER (``notBefore``/
         ``notAfter``) — mismo patrón que el cert del sello/CA en
         ``app.providers.local.verify_xades``.
      5. El responder lleva EKU ``id-kp-OCSPSigning`` Y la extensión
         ``id-pkix-ocsp-nocheck`` (1.3.6.1.5.5.7.48.1.5).
      6. La firma de la ``BasicOCSPResponse`` (RSA-SHA256) verifica contra la
         clave pública del responder.
      7. El CertID de la respuesta coincide con ``seal_cert``: mismo
         ``serial_number`` Y ``issuer_key_hash``/``issuer_name_hash``
         recomputados con ``response.hash_algorithm`` sobre ``ca_cert``
         (vía ``OCSPRequestBuilder`` — la misma construcción que produciría
         un cliente OCSP real, para no reimplementar el hashing a mano).
      8. ``certificate_status == GOOD`` — ``REVOKED`` invalida.
      9. Pinning: la huella SHA-256 del responder coincide con
         ``expected_responder_fingerprint`` (``firma.ocsp_responder_fingerprint``
         persistido al firmar) — ``None``/no coincide invalida, fail-closed,
         nunca se omite (ver docstring de módulo, "Pinning del responder").

    ``nextUpdate`` vencido NO se comprueba aquí (se EXPONE vía
    ``firma.ocsp_next_update`` para diagnóstico) — mismo criterio que
    ``crl_next_update`` en ``app.providers.local.verify_xades`` (postura no
    acreditada, material de desarrollo).
    """
    try:
        return _verify_ocsp_response_inner(
            response_der,
            seal_cert=seal_cert,
            ca_cert=ca_cert,
            expected_responder_fingerprint=expected_responder_fingerprint,
        )
    except Exception as exc:  # noqa: BLE001 — cualquier fallo == respuesta inválida (fail-closed)
        return OcspVerifyOutcome(False, motivo=f"ocsp_excepcion: {exc}")


def _verify_ocsp_response_inner(
    response_der: bytes,
    *,
    seal_cert: x509.Certificate,
    ca_cert: x509.Certificate,
    expected_responder_fingerprint: str | None,
) -> OcspVerifyOutcome:
    response = load_der_ocsp_response(response_der)

    if response.response_status != OCSPResponseStatus.SUCCESSFUL:
        return OcspVerifyOutcome(False, motivo="ocsp_response_no_exitosa")

    certificates = response.certificates
    if not certificates:
        return OcspVerifyOutcome(False, motivo="ocsp_sin_certificado_responder")
    responder = certificates[0]

    try:
        responder.verify_directly_issued_by(ca_cert)
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return OcspVerifyOutcome(False, motivo=f"ocsp_responder_no_emitido_por_ca: {exc}")

    # Ventana de vigencia del certificado del RESPONDER — mismo patrón que ya
    # se aplica al certificado del sello (app.providers.local.verify_xades,
    # "certificado_fuera_de_vigencia") y al de la CA ("ca_certificado_fuera_
    # de_vigencia"): un responder cuyo certificado expiró/aún no es válido no
    # debe poder atestiguar el estado de revocación de nadie, aunque haya
    # sido emitido legítimamente por la CA en su momento.
    if not (responder.not_valid_before_utc <= datetime.now(UTC) <= responder.not_valid_after_utc):
        return OcspVerifyOutcome(False, motivo="ocsp_responder_fuera_de_vigencia")

    try:
        eku_ext = responder.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return OcspVerifyOutcome(False, motivo="ocsp_responder_sin_eku_ocsp_signing")
    if ExtendedKeyUsageOID.OCSP_SIGNING not in eku_ext.value:
        return OcspVerifyOutcome(False, motivo="ocsp_responder_sin_eku_ocsp_signing")

    try:
        responder.extensions.get_extension_for_class(x509.OCSPNoCheck)
    except x509.ExtensionNotFound:
        return OcspVerifyOutcome(False, motivo="ocsp_responder_sin_nocheck")

    try:
        responder.public_key().verify(
            response.signature,
            response.tbs_response_bytes,
            padding.PKCS1v15(),
            response.signature_hash_algorithm,
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return OcspVerifyOutcome(False, motivo=f"ocsp_firma_invalida: {exc}")

    # `response.serial_number`/`.certificate_status`/etc. (cryptography)
    # exponen SOLO la primera `SingleResponse` de la `BasicOCSPResponse` — no
    # explotable aquí: `build_ocsp_response` (este mismo módulo) SIEMPRE
    # produce una única `SingleResponse` (un `.add_response()`, sobre
    # `seal_cert`), y una respuesta con entradas adicionales (p. ej. una
    # segunda `SingleResponse` sobre OTRO certificado antepuesta a la del
    # sello) de todos modos no pasaría las comprobaciones (3)-(9) anteriores:
    # el responder sigue pineado y encadenado a la MISMA CA del tenant, así
    # que un atacante no gana nada reordenando entradas de una respuesta
    # multi-entrada que él mismo tendría que producir y firmar.
    if response.serial_number != seal_cert.serial_number:
        return OcspVerifyOutcome(False, motivo="ocsp_certid_no_coincide")

    # Recomputa issuerNameHash/issuerKeyHash con el MISMO algoritmo declarado
    # por la respuesta, sobre ca_cert — vía OCSPRequestBuilder en vez de
    # reimplementar a mano el hashing del CertID (RFC 6960 §4.1.1).
    expected_certid = OCSPRequestBuilder().add_certificate(seal_cert, ca_cert, response.hash_algorithm).build()
    if (
        response.issuer_key_hash != expected_certid.issuer_key_hash
        or response.issuer_name_hash != expected_certid.issuer_name_hash
    ):
        return OcspVerifyOutcome(False, motivo="ocsp_certid_no_coincide")

    if response.certificate_status == OCSPCertStatus.REVOKED:
        return OcspVerifyOutcome(False, cert_status="revoked", motivo="certificado_sello_revocado_ocsp")
    if response.certificate_status != OCSPCertStatus.GOOD:
        return OcspVerifyOutcome(False, motivo="ocsp_status_desconocido")

    responder_fingerprint = responder.fingerprint(hashes.SHA256()).hex()
    if not expected_responder_fingerprint:
        return OcspVerifyOutcome(False, motivo="ocsp_responder_fingerprint_no_registrado")
    if responder_fingerprint != expected_responder_fingerprint:
        return OcspVerifyOutcome(False, motivo="ocsp_responder_fingerprint_no_coincide")

    return OcspVerifyOutcome(True, cert_status="good", produced_at=response.produced_at_utc, motivo=None)
