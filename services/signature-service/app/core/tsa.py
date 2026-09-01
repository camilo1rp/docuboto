# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""TSA (Time-Stamping Authority) RFC 3161 — XAdES-T del índice (E06 Inc.4, RT-16).

Construye y verifica el ``TimeStampToken`` (CMS ``SignedData`` con
``eContentType = id-ct-TSTInfo``, RFC 3161 §2.4.2) que
:mod:`app.providers.local` incrusta como ``xades:EncapsulatedTimeStamp`` para
elevar una firma XAdES-B a XAdES-T. Este módulo NUNCA habla HTTP con una TSA
externa en este incremento — la única implementación real es LOCAL,
in-process, con el par de claves de la TSA del propio despliegue (ver
``app.core.keys.load_tsa_seal``, ÚNICA para todo el despliegue, NO per-tenant).

--- Selección local vs remoto (``settings.tsa_url``) ---
``build_timestamp_token`` es el único punto de entrada público: si
``settings.tsa_url`` está vacío (default) construye el token LOCALMENTE; si
está puesto, delega a :func:`_request_remote_timestamp`, un cliente RFC 3161
sobre HTTP (§3.4) real. Nunca cae en silencio al camino local si el operador
configuró una URL: eso encubriría una TSA remota mal configurada declarando un
sello que en realidad nunca se solicitó.

--- Por qué NO se firma el TSTInfo con SHA-256 "a secas" ---
El ``messageImprint`` de la RFC 3161 va sobre el dato que se quiere sellar —
aquí, la canonicalización exclusiva (exc-c14n 1.0, el mismo algoritmo que
``app.providers.local`` usa para firmar) del elemento ``<ds:SignatureValue>``
YA PRODUCIDO por la firma XAdES-B, NO el XML completo ni el ``payload_sha256``
pre-firma. Esto ata el sello de tiempo a la firma concreta: alterar
``ds:SignatureValue`` DESPUÉS de sellado invalida el ``messageImprint`` (ver
:func:`verify_timestamp_token`, comprobación (b) del contrato del incremento).

--- Conformidad (RF-FIR-15) ---
La TSA LOCAL es un par autofirmado del propio despliegue, sin CA acreditada.
Da FECHA CIERTA VERIFICABLE (criptográficamente atada al acto de firma), NO
sellado de tiempo ACREDITADO/cualificado, y por eso se declara siempre
``tsa_acreditado=False`` — mire lo que mire ninguna variable de configuración.

Una TSA REMOTA (``settings.tsa_url``) sí puede serlo, porque el sellado
acreditado lo emite un prestador contratado por la entidad, no este software.
Se declara con ``settings.tsa_acreditada`` y **solo cuenta cuando hay
``tsa_url``**: el acoplamiento de las dos variables es deliberado, para que un
despliegue no pueda acreditar su TSA de pruebas poniendo un booleano.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from asn1crypto import algos, cms, core, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from app.core.config import settings
from app.core.keys import TsaSealNotFoundError, load_tsa_seal

# OID de política de sellado — NO registrado ante ninguna autoridad (IANA/AGN/
# ONAC): identifica únicamente "TSA de desarrollo OrpycaMCP, no acreditada".
# Una TSA acreditada real declararía aquí su propia política publicada.
_DEV_POLICY_OID = "1.2.3.4.5.6.7.8.9.1"


class TsaUnavailableError(RuntimeError):
    """No se pudo obtener un sello de tiempo (TSA local sin par montado, o
    remota configurada vía ``tsa_url`` sin cliente implementado en este
    incremento). El router lo traduce a 503 — ver ``app.routers.signature``.
    """


@dataclass(slots=True)
class TimestampToken:
    """Token RFC 3161 producido, listo para incrustar en el XML firmado."""

    token_der: bytes
    gen_time: datetime
    tsa_cert_fingerprint: str


@dataclass(slots=True)
class TimestampVerifyOutcome:
    """Resultado de :func:`verify_timestamp_token` — nunca lanza (fail-closed)."""

    valido: bool
    gen_time: datetime | None = None
    motivo: str | None = None


def _assert_timestamping_eku(cert: x509.Certificate) -> None:
    """Exige EKU ``id-kp-timeStamping`` CRÍTICO (requisito RFC 3161 §2.3).

    Se aplica tanto al cert de la TSA local recién cargada (antes de sellar,
    para no producir un token con un cert mal emitido) como al cert embebido
    en un token ajeno que se está verificando.
    """
    try:
        eku_ext = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound as exc:
        raise TsaUnavailableError("certificado_tsa_sin_eku_timestamping") from exc
    if not eku_ext.critical:
        raise TsaUnavailableError("certificado_tsa_eku_no_critico")
    if ExtendedKeyUsageOID.TIME_STAMPING not in eku_ext.value:
        raise TsaUnavailableError("certificado_tsa_sin_eku_timestamping")


# Estados de `PKIStatusInfo` (RFC 3161 §2.4.2) en los que la petición produjo
# un token utilizable. `granted_with_mods` significa que la TSA cambió algo de
# lo pedido (típicamente la política): el token es válido y se acepta, pero
# TODO lo que importa —el imprint, el nonce, el EKU, la firma— se comprueba
# igual más abajo, así que aceptarlo no relaja ninguna garantía.
_ESTADOS_CONCEDIDOS = frozenset({"granted", "granted_with_mods"})


def _request_remote_timestamp(signature_value_c14n: bytes) -> TimestampToken:
    """Cliente RFC 3161 sobre HTTP (§3.4) contra la TSA de ``settings.tsa_url``.

    Existe porque el sello de tiempo ACREDITADO no lo puede dar una TSA local
    por buena que sea: lo emite el prestador que la entidad contrata. Mientras
    esto fue un stub, una instalación con TSA acreditada contratada no podía
    enchufarla — la acreditación se declaraba "propia de la instalación" y la
    instalación no tenía dónde conectarla.

    Se piden **`cert_req=True`** y un **nonce**, y los dos son necesarios, no
    cortesía:

    * Sin `certReq`, la respuesta puede no incluir el certificado de la TSA, y
      entonces el token no se puede verificar más tarde de forma autónoma —
      :func:`verify_timestamp_token` exige el certificado embebido.
    * Sin **nonce**, nada distingue una respuesta fresca de la REPRODUCCIÓN de
      un token antiguo capturado en la red: la fecha cierta que se estaría
      anotando sería la de otro acto. El nonce se compara al recibir y una
      discrepancia aborta el sellado.

    El token recibido se somete además a la MISMA verificación completa que se
    le aplicará después (:func:`verify_timestamp_token`): estructura, imprint
    contra los bytes que se quisieron sellar, EKU crítico, vigencia y firma. Un
    token que no pase eso no se guarda — descubrirlo al verificar, meses
    después, sería descubrirlo cuando ya no se puede corregir.

    Es **síncrona** a propósito (ver ``settings.tsa_timeout_seconds``): el
    contrato de :func:`build_timestamp_token` lo consumen dos helpers síncronos
    del firmador, y hacerla asíncrona obligaría a propagar `async` hasta el
    interior de la construcción del XML. El precio es un bloqueo acotado por el
    timeout, en un camino que ya hace criptografía bloqueante en línea.

    Raises:
        TsaUnavailableError: la TSA no respondió, respondió un error de
            protocolo, o devolvió un token que no verifica.
    """
    imprint = hashlib.sha256(signature_value_c14n).digest()
    # 64 bits basta y sobra para el propósito (RFC 3161 §2.4.1 no fija tamaño);
    # se usa `secrets` por la misma higiene que el `serial_number` local.
    nonce = int.from_bytes(secrets.token_bytes(8), "big")
    peticion = tsp.TimeStampReq(
        {
            "version": 1,
            "message_imprint": tsp.MessageImprint(
                {
                    "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                    "hashed_message": imprint,
                }
            ),
            "nonce": nonce,
            "cert_req": True,
        }
    )

    try:
        respuesta = httpx.post(
            settings.tsa_url,
            content=peticion.dump(),
            headers={
                "Content-Type": "application/timestamp-query",
                "Accept": "application/timestamp-reply",
            },
            timeout=settings.tsa_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise TsaUnavailableError(f"tsa_remota_inalcanzable: {exc}") from exc
    if respuesta.status_code != 200:
        raise TsaUnavailableError(f"tsa_remota_http_{respuesta.status_code}")

    # La respuesta se abre como SECUENCIA GENÉRICA y no con
    # `tsp.TimeStampResp.load`, y no es una manía: asn1crypto declara
    # `timeStampToken` como campo OBLIGATORIO cuando la RFC 3161 §2.4.2 lo hace
    # OPCIONAL — precisamente porque una respuesta de RECHAZO no lleva token. Con
    # el parseo estricto, el rechazo de una TSA real reventaba al cargar y caía
    # en la rama "respuesta ilegible", perdiendo el `failInfo`, que es lo único
    # que dice qué hay que arreglar. Lo destapó el test del rechazo.
    try:
        envoltorio = core.Sequence.load(respuesta.content)
        estado_info = tsp.PKIStatusInfo.load(envoltorio[0].dump())
        estado = estado_info["status"].native
    except Exception as exc:  # noqa: BLE001 — cualquier basura == respuesta inservible
        raise TsaUnavailableError(f"tsa_remota_respuesta_ilegible: {exc}") from exc

    if estado not in _ESTADOS_CONCEDIDOS:
        # `failInfo` es opcional; cuando viene, dice POR QUÉ se rechazó
        # (`badAlg`, `timeNotAvailable`, `systemFailure`…). Se propaga porque es
        # lo único que permite a quien configuró la TSA saber qué arreglar.
        fail = estado_info["fail_info"]
        info = fail.native if not isinstance(fail, core.VOID.__class__) else None
        raise TsaUnavailableError(f"tsa_remota_rechazo: estado={estado} fail_info={info}")

    if len(envoltorio) < 2:
        raise TsaUnavailableError("tsa_remota_sin_token")
    try:
        token = cms.ContentInfo.load(envoltorio[1].dump())
        token_der = token.dump()
        tst_info = tsp.TSTInfo.load(token["content"]["encap_content_info"]["content"].contents)
    except Exception as exc:  # noqa: BLE001
        raise TsaUnavailableError(f"tsa_remota_token_ilegible: {exc}") from exc
    if tst_info["nonce"].native != nonce:
        # Reproducción, o una TSA que no respeta el nonce. En los dos casos la
        # fecha que se anotaría no es la de este acto.
        raise TsaUnavailableError("tsa_remota_nonce_no_coincide")

    certificados = token["content"]["certificates"]
    if certificados is None or len(certificados) == 0:
        raise TsaUnavailableError("tsa_remota_sin_certificado")
    tsa_cert = x509.load_der_x509_certificate(certificados[0].chosen.dump())
    huella = tsa_cert.fingerprint(hashes.SHA256()).hex()

    # El token se verifica ENTERO antes de aceptarlo, con la misma función que
    # lo verificará dentro de diez años.
    resultado = verify_timestamp_token(
        token_der, signature_value_c14n, expected_tsa_fingerprint=huella
    )
    if not resultado.valido or resultado.gen_time is None:
        raise TsaUnavailableError(f"tsa_remota_token_invalido: {resultado.motivo}")

    return TimestampToken(
        token_der=token_der, gen_time=resultado.gen_time, tsa_cert_fingerprint=huella
    )


def _build_local_timestamp_token(signature_value_c14n: bytes) -> TimestampToken:
    """TSA LOCAL in-process: construye un ``TimeStampToken`` CMS completo.

    Estructura (RFC 3161 §2.4.2, RFC 5652 §5):
    ``ContentInfo{contentType=signedData, content=SignedData{
        encapContentInfo{eContentType=id-ct-TSTInfo, eContent=DER(TSTInfo)},
        certificates=[cert TSA], signerInfos=[SignerInfo firmado RSA-SHA256]
    }}``. Serializado a DER — ``TimestampToken.token_der`` es exactamente lo
    que ``app.providers.local`` incrusta en base64 dentro de
    ``xades:EncapsulatedTimeStamp``.
    """
    try:
        tsa_ref = load_tsa_seal()
    except TsaSealNotFoundError as exc:
        raise TsaUnavailableError(str(exc)) from exc

    tsa_cert = x509.load_pem_x509_certificate(tsa_ref.cert_pem)
    _assert_timestamping_eku(tsa_cert)
    private_key = serialization.load_pem_private_key(tsa_ref.key_pem, password=tsa_ref.key_password)
    # `load_pem_private_key` devuelve la unión de TODOS los tipos de clave que
    # soporta `cryptography` (RSA, EC, Ed25519, DH, ML-DSA…), pero más abajo se
    # firma con `padding.PKCS1v15()`, que solo existe para RSA. Sin esta
    # comprobación, una clave de TSA no-RSA reventaba dentro de `sign()` con un
    # error del backend criptográfico, no con un fallo atribuible a la
    # configuración del sello. Se falla aquí, con motivo legible.
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TsaUnavailableError(
            "la clave del sello TSA local no es RSA; el token RFC 3161 se firma "
            f"con PKCS#1 v1.5 y no admite {type(private_key).__name__}"
        )

    gen_time = datetime.now(UTC)
    message_imprint = tsp.MessageImprint(
        {
            "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
            "hashed_message": hashlib.sha256(signature_value_c14n).digest(),
        }
    )
    tst_info = tsp.TSTInfo(
        {
            "version": 1,
            "policy": _DEV_POLICY_OID,
            "message_imprint": message_imprint,
            # CSPRNG (secrets, NO random/Math.random) — RFC 3161 exige unicidad
            # por TSA, no aleatoriedad criptográfica per se, pero se usa
            # secrets de todas formas por higiene general del proyecto.
            "serial_number": int.from_bytes(secrets.token_bytes(16), "big"),
            "gen_time": gen_time,
            "accuracy": tsp.Accuracy({"seconds": 1}),
            "ordering": False,
        }
    )
    tst_info_der = tst_info.dump()

    asn1_cert = asn1_x509.Certificate.load(tsa_cert.public_bytes(serialization.Encoding.DER))

    # signedAttrs firmados (RFC 5652 §5.4): contentType + messageDigest(eContent)
    # + signingTime. Se firman como SET OF DER — ver la nota de re-tagging en
    # verify_timestamp_token (mismo truco al reconstruir para verificar).
    signed_attrs = cms.CMSAttributes(
        [
            cms.CMSAttribute(
                {"type": "content_type", "values": cms.SetOfContentType([cms.ContentType("tst_info")])}
            ),
            cms.CMSAttribute(
                {
                    "type": "message_digest",
                    "values": cms.SetOfOctetString([core.OctetString(hashlib.sha256(tst_info_der).digest())]),
                }
            ),
            cms.CMSAttribute({"type": "signing_time", "values": cms.SetOfTime([cms.Time({"utc_time": gen_time})])}),
        ]
    )
    to_sign = signed_attrs.dump()
    signature = private_key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())

    signer_info = cms.SignerInfo(
        {
            "version": 1,
            "sid": cms.SignerIdentifier(
                {
                    "issuer_and_serial_number": cms.IssuerAndSerialNumber(
                        {"issuer": asn1_cert.issuer, "serial_number": asn1_cert.serial_number}
                    )
                }
            ),
            "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
            "signed_attrs": signed_attrs,
            "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "rsassa_pkcs1v15"}),
            "signature": signature,
        }
    )
    signed_data = cms.SignedData(
        {
            "version": 3,
            "digest_algorithms": cms.DigestAlgorithms([algos.DigestAlgorithm({"algorithm": "sha256"})]),
            "encap_content_info": cms.EncapsulatedContentInfo(
                {"content_type": "tst_info", "content": core.ParsableOctetString(tst_info_der)}
            ),
            "certificates": cms.CertificateSet([cms.CertificateChoices({"certificate": asn1_cert})]),
            "signer_infos": cms.SignerInfos([signer_info]),
        }
    )
    content_info = cms.ContentInfo({"content_type": "signed_data", "content": signed_data})

    return TimestampToken(
        token_der=content_info.dump(),
        gen_time=gen_time,
        tsa_cert_fingerprint=tsa_cert.fingerprint(hashes.SHA256()).hex(),
    )


def sello_acreditado() -> bool:
    """¿El sello de tiempo que produce este módulo es de un prestador acreditado?

    **Las dos condiciones van juntas**, y ese acoplamiento es el control: una
    TSA local acreditada no existe —es un par autofirmado del propio
    despliegue—, así que el booleano por sí solo no puede acreditar nada. Sin
    esto, `TSA_ACREDITADA=true` en un entorno de pruebas habría bastado para que
    el sistema declarara fecha cierta oponible sobre sellos que emite él mismo.
    """
    return bool(settings.tsa_url) and settings.tsa_acreditada


def build_timestamp_token(signature_value_c14n: bytes) -> TimestampToken:
    """Obtiene un sello de tiempo RFC 3161 sobre ``signature_value_c14n``.

    ``signature_value_c14n`` YA debe venir canonicalizado (exc-c14n 1.0) por
    el llamador (``app.providers.local.LocalSigner.sign``) — este módulo no
    conoce XML, solo bytes a sellar. Despacha a TSA local o al stub remoto
    según ``settings.tsa_url`` (ver docstring del módulo).

    Raises:
        TsaUnavailableError: ninguna TSA (local ni remota) pudo sellar.
    """
    if settings.tsa_url:
        return _request_remote_timestamp(signature_value_c14n)
    return _build_local_timestamp_token(signature_value_c14n)


def verify_timestamp_token(
    token_der: bytes,
    signature_value_c14n: bytes,
    *,
    expected_tsa_fingerprint: str | None,
) -> TimestampVerifyOutcome:
    """Verifica un ``TimeStampToken`` RFC 3161 (E06 Inc.4, RT-16).

    Fail-closed — cualquier fallo estructural o criptográfico devuelve
    ``valido=False`` con un ``motivo``, nunca lanza (mismo contrato que
    ``app.providers.local.verify_xades``). Comprueba, en orden:

      1. El token es un CMS ``SignedData`` con ``eContentType=id-ct-TSTInfo``.
      2. El ``messageImprint`` (SHA-256) coincide con
         ``sha256(signature_value_c14n)`` — ata el sello al
         ``ds:SignatureValue`` ACTUAL: si éste fue alterado después de
         sellado, esta comprobación falla (RT-16, caso (b) del incremento).
      3. El certificado embebido de la TSA tiene EKU ``timeStamping`` crítico
         y está vigente en el ``genTime`` declarado.
      4. Su huella SHA-256 coincide con ``expected_tsa_fingerprint`` — el
         ``firma.tsa_cert_fingerprint`` persistido al sellar (ancla de
         confianza, migración 012). Sin esta comprobación, ``xades:
         SignatureTimeStamp`` es una propiedad NO FIRMADA (ver
         ``app.providers.local``): cualquiera con un XML XAdES-B genuino del
         tenant podría añadirle un token sellado con SU PROPIA TSA (EKU
         timeStamping autofirmado) y `/verify` lo reportaría válido con un
         `genTime` arbitrario. ``expected_tsa_fingerprint=None`` (fila legacy
         sin columna poblada, o ``verify_xades`` llamado sin ese dato)
         invalida el sello — fail-closed, nunca se omite la comprobación.
      5. Los ``signedAttrs`` (``messageDigest`` sobre el ``eContent``) y la
         firma CMS (RSA-SHA256) verifican contra el certificado embebido.

    NO valida cadena de confianza contra una CA raíz (misma postura que
    ``verify_xades``: sin CA acreditada ONAC, no hay cadena que validar) —
    por eso el llamador (``app.providers.local``) nunca sube ``acreditado`` a
    ``True`` a partir de este resultado.
    """
    try:
        return _verify_timestamp_token_inner(
            token_der, signature_value_c14n, expected_tsa_fingerprint=expected_tsa_fingerprint
        )
    except Exception as exc:  # noqa: BLE001 — cualquier fallo == sello inválido (fail-closed)
        return TimestampVerifyOutcome(False, motivo=f"sello_tiempo_invalido: {exc}")


def _verify_timestamp_token_inner(
    token_der: bytes, signature_value_c14n: bytes, *, expected_tsa_fingerprint: str | None
) -> TimestampVerifyOutcome:
    content_info = cms.ContentInfo.load(token_der)
    if content_info["content_type"].native != "signed_data":
        return TimestampVerifyOutcome(False, motivo="token_no_es_signed_data")

    signed_data = content_info["content"]
    encap = signed_data["encap_content_info"]
    if encap["content_type"].native != "tst_info":
        return TimestampVerifyOutcome(False, motivo="token_no_es_tst_info")

    tst_info_der = encap["content"].contents
    tst_info = tsp.TSTInfo.load(tst_info_der)

    message_imprint = tst_info["message_imprint"]
    if message_imprint["hash_algorithm"]["algorithm"].native != "sha256":
        return TimestampVerifyOutcome(False, motivo="message_imprint_algoritmo_no_soportado")
    if message_imprint["hashed_message"].native != hashlib.sha256(signature_value_c14n).digest():
        return TimestampVerifyOutcome(False, motivo="message_imprint_no_coincide")

    signer_infos = signed_data["signer_infos"]
    if len(signer_infos) != 1:
        return TimestampVerifyOutcome(False, motivo="token_signer_infos_inesperado")
    signer_info = signer_infos[0]

    certificates = signed_data["certificates"]
    if certificates is None or len(certificates) == 0:
        return TimestampVerifyOutcome(False, motivo="token_sin_certificado_tsa")
    tsa_cert = x509.load_der_x509_certificate(certificates[0].chosen.dump())

    try:
        _assert_timestamping_eku(tsa_cert)
    except TsaUnavailableError as exc:
        return TimestampVerifyOutcome(False, motivo=str(exc))

    # (4) Ancla de confianza — ver docstring de este módulo, punto 4. Sin
    # comparar contra el fingerprint PERSISTIDO al sellar, el certificado
    # embebido en el token no prueba nada por sí solo (nada impide que un
    # atacante genere su propio par autofirmado con EKU timeStamping crítico
    # y lo use para sellar un `ds:SignatureValue` ajeno). Fail-closed también
    # cuando no hay fingerprint registrado (fila legacy) — nunca se omite.
    tsa_cert_fingerprint = tsa_cert.fingerprint(hashes.SHA256()).hex()
    if not expected_tsa_fingerprint:
        return TimestampVerifyOutcome(False, motivo="tsa_fingerprint_no_registrado")
    if tsa_cert_fingerprint != expected_tsa_fingerprint:
        return TimestampVerifyOutcome(False, motivo="tsa_cert_fingerprint_no_coincide")

    gen_time = tst_info["gen_time"].native
    if not (tsa_cert.not_valid_before_utc <= gen_time <= tsa_cert.not_valid_after_utc):
        return TimestampVerifyOutcome(False, motivo="certificado_tsa_fuera_de_vigencia_al_sellar")

    # NO se comprueba aquí que `gen_time` caiga también dentro de la vigencia
    # del certificado FIRMANTE del índice (el sello institucional) — en
    # XAdES-T ese cruce es lo que atestigua que la firma existió mientras el
    # certificado de firma era válido. No es exigible para el perfil local de
    # desarrollo (`tsa_acreditado` ya es False sin excepción) y el llamador
    # (`app.providers.local.verify_xades`) no tiene aquí el certificado del
    # firmante a mano sin refactorizar la firma de esta función — queda como
    # roadmap explícito para cuando entre la TSA acreditada / perfil
    # XAdES-LT/-LTA, no bloqueante en el Incremento 4.
    signed_attrs = signer_info["signed_attrs"]
    if signed_attrs is None:
        return TimestampVerifyOutcome(False, motivo="token_sin_signed_attrs")

    # Re-tagging: `signed_attrs`, leído desde `SignerInfo`, lleva la etiqueta
    # IMPLICIT [0] de ese campo — el valor firmado en realidad es la
    # codificación DER como SET OF universal (RFC 5652 §5.4). Envolver en una
    # instancia nueva de CMSAttributes re-serializa con la etiqueta correcta.
    fresh_attrs = cms.CMSAttributes(signed_attrs)

    content_type_attr: str | None = None
    message_digest_attr: bytes | None = None
    for attr in fresh_attrs:
        if attr["type"].native == "content_type":
            content_type_attr = attr["values"][0].native
        elif attr["type"].native == "message_digest":
            message_digest_attr = attr["values"][0].native

    if content_type_attr != "tst_info":
        return TimestampVerifyOutcome(False, motivo="signed_attrs_content_type_no_coincide")
    if message_digest_attr != hashlib.sha256(tst_info_der).digest():
        return TimestampVerifyOutcome(False, motivo="signed_attrs_message_digest_no_coincide")

    if signer_info["digest_algorithm"]["algorithm"].native != "sha256":
        return TimestampVerifyOutcome(False, motivo="digest_algorithm_no_soportado")
    if signer_info["signature_algorithm"]["algorithm"].native != "rsassa_pkcs1v15":
        return TimestampVerifyOutcome(False, motivo="signature_algorithm_no_soportado")

    to_verify = fresh_attrs.dump()
    # El token DECLARA `rsassa_pkcs1v15` (comprobado arriba), pero eso es el
    # algoritmo declarado, no el tipo real de la clave del certificado TSA.
    # Se comprueba la clave antes de verificar: sin esto, un certificado con
    # clave no-RSA caía en el `except` de abajo con un motivo del backend
    # criptográfico en vez de uno atribuible.
    tsa_public_key = tsa_cert.public_key()
    if not isinstance(tsa_public_key, rsa.RSAPublicKey):
        return TimestampVerifyOutcome(False, motivo="clave_tsa_no_rsa")
    try:
        tsa_public_key.verify(
            signer_info["signature"].native, to_verify, padding.PKCS1v15(), hashes.SHA256()
        )
    except Exception as exc:  # noqa: BLE001 — cualquier fallo criptográfico == inválido
        return TimestampVerifyOutcome(False, motivo=f"firma_cms_invalida: {exc}")

    return TimestampVerifyOutcome(True, gen_time=gen_time, motivo=None)
