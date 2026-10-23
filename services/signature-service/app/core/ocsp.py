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

--- Dos usos distintos de OCSP en este módulo ---
(1) OCSP *stapled* sobre el sello institucional (``build_ocsp_response`` /
``verify_ocsp_response``, E06): el responder es nuestro, pineado, y no hay petición.
(2) OCSP *en línea* sobre el certificado de la TSA anterior (``build_ocsp_request_tsa`` /
``verify_ocsp_tsa``, C-6, ADR-042): el responder es de un tercero, el ancla es su CA y sí hay
petición con nonce. Este módulo sigue sin hacer HTTP: la petición la envía ``app.core.tsa``.
Lo que sigue de la cabecera describe el uso (1).

--- Modelo de confianza: responder DELEGADO, coherente con la CRL ---
En el uso (1) este módulo NUNCA habla HTTP con un responder OCSP externo — el "responder"
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
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.ocsp import (
    OCSPCertStatus,
    OCSPRequestBuilder,
    OCSPResponderEncoding,
    OCSPResponseBuilder,
    OCSPResponseStatus,
    load_der_ocsp_response,
)
from cryptography.x509.oid import ExtendedKeyUsageOID, SignatureAlgorithmOID

from app.core.algoritmos import identificador_de_clave
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


def _exigir_delegado(
    responder: x509.Certificate, ca_cert: x509.Certificate, instante: datetime
) -> str | None:
    """Motivo por el que ``responder`` no es un respondedor DELEGADO válido de ``ca_cert`` en
    ``instante``, o ``None`` si lo es. Lógica común al OCSP stapled del sello (:func:`verify_ocsp_response`)
    y al OCSP de la TSA (:func:`verify_ocsp_tsa`). Comprueba, en orden: emitido DIRECTAMENTE por la
    CA; vigente en ``instante`` (un respondedor caducado no atestigua nada aunque la CA lo emitiera);
    EKU ``id-kp-OCSPSigning`` (sin él, cualquier certificado de la CA firmaría respuestas); y
    ``id-pkix-ocsp-nocheck`` (RFC 6960 §4.2.2.2)."""
    try:
        responder.verify_directly_issued_by(ca_cert)
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return f"ocsp_responder_no_emitido_por_ca: {exc}"

    # Ventana de vigencia del certificado del RESPONDER — mismo patrón que ya
    # se aplica al certificado del sello (app.providers.local.verify_xades,
    # "certificado_fuera_de_vigencia") y al de la CA ("ca_certificado_fuera_
    # de_vigencia"): un responder cuyo certificado expiró/aún no es válido no
    # debe poder atestiguar el estado de revocación de nadie, aunque haya
    # sido emitido legítimamente por la CA en su momento.
    if not (responder.not_valid_before_utc <= instante <= responder.not_valid_after_utc):
        return "ocsp_responder_fuera_de_vigencia"

    try:
        eku_ext = responder.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return "ocsp_responder_sin_eku_ocsp_signing"
    if ExtendedKeyUsageOID.OCSP_SIGNING not in eku_ext.value:
        return "ocsp_responder_sin_eku_ocsp_signing"

    try:
        responder.extensions.get_extension_for_class(x509.OCSPNoCheck)
    except x509.ExtensionNotFound:
        return "ocsp_responder_sin_nocheck"
    return None


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

    motivo_delegado = _exigir_delegado(responder, ca_cert, datetime.now(UTC))
    if motivo_delegado is not None:
        return OcspVerifyOutcome(False, motivo=motivo_delegado)

    # `public_key()` devuelve la unión de todos los tipos de clave soportados;
    # abajo se verifica con `padding.PKCS1v15()`, que solo existe para RSA. Se
    # estrecha ANTES de verificar en vez de dejarlo caer en el `except`: aquí se
    # decide si un certificado está revocado, y "no pude verificar la firma del
    # respondedor" tiene que ser un motivo propio, no un error genérico del
    # backend. Sigue siendo fail-closed — un respondedor no-RSA no se acepta.
    responder_key = responder.public_key()
    if not isinstance(responder_key, rsa.RSAPublicKey):
        return OcspVerifyOutcome(False, motivo="ocsp_responder_clave_no_rsa")
    # `signature_hash_algorithm` es opcional en el modelo de `cryptography`;
    # sin él no hay nada que verificar y aceptar sería aceptar a ciegas.
    if response.signature_hash_algorithm is None:
        return OcspVerifyOutcome(False, motivo="ocsp_respuesta_sin_hash_algorithm")
    try:
        responder_key.verify(
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


# ---------------------------------------------------------------------------
# OCSP EN LÍNEA de la TSA (C-6, ADR-034 §5)
# ---------------------------------------------------------------------------
#
# Lo anterior es OCSP *stapled* sobre el sello institucional: el respondedor es nuestro y está
# pineado. Lo de aquí es otra cosa: la revocación del certificado de la TSA del sello de archivo
# ANTERIOR cuando su CA no publica CRL. El respondedor es el de un tercero; el ancla es la CA
# (el propio respondedor de la CA, o un delegado emitido directamente por ella), no una huella
# anotada de antemano. Se pregunta con nonce y la respuesta se conserva en el
# ``TimeStampValidationData`` para que el sello anterior se valide sin consultar a nadie.

_HOLGURA_RELOJ = timedelta(minutes=5)
OCSP_TSA_MAX_BYTES = 64 * 1024
_OCSP_TSA_HASH = hashes.SHA256()


@dataclass(slots=True)
class OcspTsaOutcome:
    """Resultado de :func:`verify_ocsp_tsa` — nunca lanza (fail-closed).

    ``valido`` dice que la respuesta es AUTÉNTICA, del certificado preguntado y está en plazo;
    no dice que el certificado esté bien: eso es ``estado`` (``good`` | ``revoked``). Quien
    consume decide qué hacer con un ``revoked`` según CUÁNDO se revocó (``revocation_time``)."""

    valido: bool
    estado: str | None = None
    revocation_time: datetime | None = None
    produced_at: datetime | None = None
    motivo: str | None = None
    # Identificadores de catálogo (:mod:`app.core.algoritmos`) de la firma de la respuesta; los
    # evalúa el llamante en la fecha que corresponda (hoy al renovar; la del sello siguiente al verificar).
    algoritmos: tuple[str, ...] = ()


def urls_ocsp_de(cert: x509.Certificate) -> list[str]:
    """URL ``http(s)`` del OCSP anunciado en la extensión AIA del certificado (sin red)."""
    try:
        aia = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
    except x509.ExtensionNotFound:
        return []
    return [
        d.access_location.value
        for d in aia
        if d.access_method == x509.AuthorityInformationAccessOID.OCSP
        and isinstance(d.access_location, x509.UniformResourceIdentifier)
        and d.access_location.value.lower().startswith(("http://", "https://"))
    ]


def build_ocsp_request_tsa(cert: x509.Certificate, issuer: x509.Certificate) -> tuple[bytes, bytes]:
    """Petición OCSP (RFC 6960) con CertID SHA-256 y nonce de 32 bytes (RFC 8954).
    Devuelve ``(petición_der, nonce)``."""
    import secrets

    nonce = secrets.token_bytes(32)
    request = (
        OCSPRequestBuilder()
        .add_certificate(cert, issuer, _OCSP_TSA_HASH)
        .add_extension(x509.OCSPNonce(nonce), critical=False)
        .build()
    )
    return request.public_bytes(serialization.Encoding.DER), nonce


def _id_de_respondedor_coincide(response: object, candidato: x509.Certificate) -> bool:
    nombre = getattr(response, "responder_name", None)
    if nombre is not None:
        return bool(nombre == candidato.subject)
    huella = getattr(response, "responder_key_hash", None)
    if huella is None:
        return False
    return bool(huella == x509.SubjectKeyIdentifier.from_public_key(candidato.public_key()).digest)


def _verificar_firma_respuesta(response: object, clave: object) -> str | None:
    """Motivo si la firma de la ``BasicOCSPResponse`` no verifica con ``clave``; ``None`` si sí."""
    hash_alg = getattr(response, "signature_hash_algorithm", None)
    if hash_alg is None:
        return "ocsp_respuesta_sin_hash_algorithm"
    firma = response.signature  # type: ignore[attr-defined]
    datos = response.tbs_response_bytes  # type: ignore[attr-defined]
    try:
        if isinstance(clave, rsa.RSAPublicKey):
            # La librería no expone los parámetros de RSASSA-PSS (sal): solo PKCS#1 v1.5 se puede
            # comprobar aquí; PSS se rechaza en vez de verificarlo mal.
            if response.signature_algorithm_oid == SignatureAlgorithmOID.RSASSA_PSS:  # type: ignore[attr-defined]
                return "ocsp_rsa_pss_no_soportado"
            clave.verify(firma, datos, padding.PKCS1v15(), hash_alg)
        elif isinstance(clave, ec.EllipticCurvePublicKey):
            clave.verify(firma, datos, ec.ECDSA(hash_alg))
        else:
            return "ocsp_clave_de_firma_no_soportada"
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return f"ocsp_firma_invalida: {exc}"
    return None


def verify_ocsp_tsa(
    response_der: bytes,
    cert: x509.Certificate,
    issuer: x509.Certificate,
    *,
    nonce: bytes | None = None,
    ventana: tuple[datetime, datetime] | None = None,
) -> OcspTsaOutcome:
    """Verifica la respuesta OCSP sobre el certificado de una TSA. Nunca lanza.

    Comprueba: ``SUCCESSFUL``; una ``SingleResponse`` cuyo CertID coincide con ``cert``/``issuer``
    (se BUSCA entre todas, no se toma la primera); firmante = la propia CA o un delegado emitido
    directamente por ella (EKU ``OCSPSigning`` + ``ocsp-nocheck`` + vigente); firma verificada con
    su clave; estado ``good``/``revoked`` (``unknown`` no vale).

    Dos modos, según ``ventana``:

      * EN LÍNEA (``ventana=None``, al renovar): frescura respecto de AHORA —``thisUpdate`` no está
        en el futuro, ``nextUpdate`` no venció— y nonce: si la respuesta trae el de ``nonce`` debe
        coincidir; si no trae ninguno se acepta SOLO si ``producedAt`` está a ±5 min de ahora
        (muchos respondedores de TSA no devuelven nonce; una respuesta vieja reenviada no pasa).
      * ARCHIVADA (``ventana=(sello_previo, sello_siguiente)``, al verificar): no hay «ahora»; la
        respuesta tiene que haberse producido DESPUÉS del sello cuyo certificado cubre y haber
        existido cuando se selló encima: ``previo <= producedAt <= siguiente``.
    """
    try:
        return _verify_ocsp_tsa_inner(response_der, cert, issuer, nonce=nonce, ventana=ventana)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo == respuesta inválida
        return OcspTsaOutcome(False, motivo=f"ocsp_excepcion: {exc}")


def _verify_ocsp_tsa_inner(
    response_der: bytes,
    cert: x509.Certificate,
    issuer: x509.Certificate,
    *,
    nonce: bytes | None,
    ventana: tuple[datetime, datetime] | None,
) -> OcspTsaOutcome:
    response = load_der_ocsp_response(response_der)
    if response.response_status != OCSPResponseStatus.SUCCESSFUL:
        return OcspTsaOutcome(False, motivo="ocsp_response_no_exitosa")

    ahora = datetime.now(UTC)
    produced_at = response.produced_at_utc
    # El instante con que se juzga al respondedor: el de la producción de la respuesta.
    instante = ahora if ventana is None else produced_at

    # --- quién firma -------------------------------------------------------------------------
    if _id_de_respondedor_coincide(response, issuer):
        if not (issuer.not_valid_before_utc <= instante <= issuer.not_valid_after_utc):
            return OcspTsaOutcome(False, motivo="ocsp_ca_fuera_de_vigencia")
        firmante = issuer
    else:
        delegado = next(
            (c for c in (response.certificates or []) if _id_de_respondedor_coincide(response, c)), None
        )
        if delegado is None:
            return OcspTsaOutcome(False, motivo="ocsp_respondedor_ni_la_ca_ni_delegado")
        motivo = _exigir_delegado(delegado, issuer, instante)
        if motivo is not None:
            return OcspTsaOutcome(False, motivo=motivo)
        firmante = delegado
    motivo_firma = _verificar_firma_respuesta(response, firmante.public_key())
    if motivo_firma is not None:
        return OcspTsaOutcome(False, motivo=motivo_firma)
    hash_alg = response.signature_hash_algorithm
    algoritmos = (
        hash_alg.name if hash_alg is not None else "sin-digest",
        identificador_de_clave(firmante.public_key()),
    )

    # --- sobre qué certificado ---------------------------------------------------------------
    elegida = None
    for single in response.responses:
        if single.serial_number != cert.serial_number:
            continue
        esperado = OCSPRequestBuilder().add_certificate(cert, issuer, single.hash_algorithm).build()
        if (
            single.issuer_key_hash == esperado.issuer_key_hash
            and single.issuer_name_hash == esperado.issuer_name_hash
        ):
            elegida = single
            break
    if elegida is None:
        return OcspTsaOutcome(False, motivo="ocsp_certid_no_coincide", algoritmos=algoritmos)

    # --- frescura / nonce --------------------------------------------------------------------
    if ventana is None:
        if elegida.this_update_utc > ahora + _HOLGURA_RELOJ:
            return OcspTsaOutcome(False, motivo="ocsp_this_update_en_el_futuro", algoritmos=algoritmos)
        if elegida.next_update_utc is not None and elegida.next_update_utc < ahora:
            return OcspTsaOutcome(False, motivo="ocsp_vencida", algoritmos=algoritmos)
        try:
            nonce_resp = response.extensions.get_extension_for_class(x509.OCSPNonce).value.nonce
        except x509.ExtensionNotFound:
            nonce_resp = None
        if nonce is not None and nonce_resp is not None:
            if nonce_resp != nonce:
                return OcspTsaOutcome(False, motivo="ocsp_nonce_no_coincide", algoritmos=algoritmos)
        elif abs(produced_at - ahora) > _HOLGURA_RELOJ:
            return OcspTsaOutcome(False, motivo="ocsp_sin_nonce_y_no_fresca", algoritmos=algoritmos)
    else:
        previo, siguiente = ventana
        if produced_at < previo:
            return OcspTsaOutcome(False, motivo="ocsp_anterior_al_sello_previo", algoritmos=algoritmos)
        if produced_at > siguiente or elegida.this_update_utc > siguiente:
            return OcspTsaOutcome(False, motivo="ocsp_posterior_al_sello_siguiente", algoritmos=algoritmos)

    # --- estado ------------------------------------------------------------------------------
    if elegida.certificate_status == OCSPCertStatus.GOOD:
        return OcspTsaOutcome(True, "good", None, produced_at, None, algoritmos)
    if elegida.certificate_status == OCSPCertStatus.REVOKED:
        return OcspTsaOutcome(True, "revoked", elegida.revocation_time_utc, produced_at, None, algoritmos)
    return OcspTsaOutcome(False, motivo="ocsp_status_desconocido", algoritmos=algoritmos)
