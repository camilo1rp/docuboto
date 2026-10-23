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
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from asn1crypto import algos, cms, core, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from app.core import algoritmos as catalogo
from app.core import ocsp
from app.core.config import settings
from app.core.keys import TsaSealNotFoundError, load_tsa_seal

# OID de política de sellado — NO registrado ante ninguna autoridad (IANA/AGN/
# ONAC): identifica únicamente "TSA de desarrollo OrpycaMCP, no acreditada".
# Una TSA acreditada real declararía aquí su propia política publicada.
_DEV_POLICY_OID = "1.2.3.4.5.6.7.8.9.1"


class TsaUnavailableError(RuntimeError):
    """No se pudo obtener un sello de tiempo (TSA local sin par montado, o
    remota configurada vía ``tsa_url`` que no respondió, no devolvió un token
    conforme —nonce, EKU, ESSCertID— o excedió el tiempo). El router lo traduce a 503 — ver ``app.routers.signature``.
    """


@dataclass(slots=True)
class TimestampToken:
    """Token RFC 3161 producido, listo para incrustar en el XML firmado."""

    token_der: bytes
    gen_time: datetime
    tsa_cert_fingerprint: str


#: Advertencia de `/verify`: sello válido cuyo token no lleva ESSCertID porque es
#: anterior a `settings.tsa_esscertid_corte`.
ADVERTENCIA_SIN_ESSCERTID = "tsa_sin_esscertid_anterior_al_corte"


@dataclass(slots=True)
class TimestampVerifyOutcome:
    """Resultado de :func:`verify_timestamp_token` — nunca lanza (fail-closed)."""

    valido: bool
    gen_time: datetime | None = None
    motivo: str | None = None
    # Avisos sobre un sello que SÍ es válido (hoy: sin ESSCertID pero anterior a
    # la fecha de corte). `/verify` los publica; no cambian el veredicto.
    advertencias: list[str] = field(default_factory=list)
    # Fin de vigencia del certificado de la TSA que firmó el token. La cadena de
    # `ArchiveTimeStamp` (B10 §8) lo necesita: cada sello debe renovarse ANTES de que
    # caduque el certificado de su TSA.
    tsa_cert_not_after: datetime | None = None
    # Identificadores de catálogo (:mod:`app.core.algoritmos`) que usó el token: digest y clave de la
    # TSA. El llamante los juzga en la fecha que toque (regla temporal de EN 319 102-1 §5.6.2.5).
    algoritmos: tuple[str, ...] = ()


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
    esto fue un stub (ya no lo es), una instalación con TSA acreditada contratada no podía
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
            # RFC 5035 / RFC 3161 §2.4.2: el token declara QUÉ certificado de TSA
            # lo firmó (ESSCertIDv2, SHA-256). Sin él, nada dentro de lo firmado
            # liga el token a un certificado concreto (AREA-04-08).
            cms.CMSAttribute(
                {
                    "type": "signing_certificate_v2",
                    "values": tsp.SetOfSigningCertificatesV2(
                        [
                            tsp.SigningCertificateV2(
                                {
                                    "certs": [
                                        tsp.ESSCertIDv2(
                                            {
                                                "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                                                "cert_hash": hashlib.sha256(asn1_cert.dump()).digest(),
                                            }
                                        )
                                    ]
                                }
                            )
                        ]
                    ),
                }
            ),
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


def certificados_de_token(token_der: bytes) -> list[x509.Certificate]:
    """Certificados que el ``TimeStampToken`` lleva embebidos (el primero es el de la TSA).

    Sirve a la renovación del ``ArchiveTimeStamp`` (B10 §8): el ``TimeStampValidationData`` que
    precede al sello nuevo guarda la cadena de la TSA del sello anterior, de modo que el
    anterior siga siendo verificable aunque su TSA ya no responda."""
    content_info = cms.ContentInfo.load(token_der)
    certificates = content_info["content"]["certificates"]
    if certificates is None:
        return []
    return [x509.load_der_x509_certificate(c.chosen.dump()) for c in certificates]


def gen_time_de_token(token_der: bytes) -> datetime:
    """``genTime`` declarado en el ``TSTInfo`` de un token (sin verificarlo: lo verifica
    :func:`verify_timestamp_token`). Sirve para fijar desde cuándo vale la evidencia de revocación."""
    content = cms.ContentInfo.load(token_der)["content"]
    tst = tsp.TSTInfo.load(content["encap_content_info"]["content"].contents)
    return tst["gen_time"].native


def certificado_tsa_de_token(token_der: bytes) -> x509.Certificate | None:
    """El certificado de la TSA que FIRMÓ el token (``SignerInfo.sid``), no «el primero».

    ``certificates`` de un CMS ``SignedData`` es un ``SET OF``: su orden no está garantizado
    (DER lo ordena por codificación) y una TSA remota puede incluir la cadena, con la CA delante.
    Se busca por emisor + número de serie del ``sid``; si el ``sid`` es un ``SubjectKeyIdentifier``
    se cae al certificado con EKU ``timeStamping``. ``None`` si el token no trae certificados o
    ninguno casa (el llamante decide: nunca se adivina)."""
    certs = certificados_de_token(token_der)
    if not certs:
        return None
    try:
        content = cms.ContentInfo.load(token_der)["content"]
        sid = content["signer_infos"][0]["sid"].chosen
        if isinstance(sid, cms.IssuerAndSerialNumber):
            serie = sid["serial_number"].native
            emisor = sid["issuer"].dump()
            for c in certs:
                if c.serial_number == serie and c.issuer.public_bytes() == emisor:
                    return c
            return None
    except Exception:  # noqa: BLE001 — token raro: se intenta por EKU
        pass
    for c in certs:
        try:
            _assert_timestamping_eku(c)
        except TsaUnavailableError:
            continue
        return c
    return None


class RevocacionTsaNoDisponibleError(RuntimeError):
    """No se pudo obtener, o no se pudo VERIFICAR, la revocación (CRL u OCSP) de la CA que emitió el
    certificado de la TSA del sello anterior: ``TimeStampValidationData`` (EN 319 132-1 §5.5.1)
    no puede completarse y la renovación del sello de archivo NO debe seguir."""


class TsaRevocadaError(RuntimeError):
    """El certificado de la TSA del sello anterior figura REVOCADO (CRL u OCSP) ANTES de que
    sellara: renovar sellaría una cadena que un validador tercero no daría por buena."""


_CRL_MAX_BYTES = 5 * 1024 * 1024

#: Fuentes de revocación que un certificado de TSA puede ofrecer (columna ``tsa_revocacion_fuente``).
FUENTE_CRL = "crl"
FUENTE_OCSP = "ocsp"
FUENTE_CRL_OCSP = "crl_ocsp"
FUENTE_AUTOFIRMADA = "autofirmada"
FUENTE_NINGUNA = "ninguna"


@dataclass(slots=True)
class RevocacionTsa:
    """Material de revocación del certificado de la TSA del sello anterior, listo para el
    ``TimeStampValidationData``. ``fuente``: ``crl`` | ``ocsp`` | ``autofirmada`` (la TSA local es su
    propia ancla: sin CA no hay revocación que pedir, y ADR-034 §5 lo declara)."""

    fuente: str
    crl_der: bytes | None = None
    ocsp_der: bytes | None = None
    # La CA que verificó la revocación. El token de una TSA puede no traerla (la TSA local no tiene
    # CA; una remota entrega a veces solo su hoja): el `TimeStampValidationData` la lleva igual, o
    # quien lo valide dentro de diez años no tendría con qué comprobar la firma de la CRL u OCSP.
    cadena: tuple[x509.Certificate, ...] = ()


def _cas_configuradas() -> list[x509.Certificate]:
    cas: list[x509.Certificate] = []
    for ruta in (r.strip() for r in settings.tsa_ca_pem_paths.split(",")):
        if not ruta:
            continue
        try:
            with open(ruta, "rb") as f:
                cas.extend(x509.load_pem_x509_certificates(f.read()))
        except (OSError, ValueError) as exc:
            raise RevocacionTsaNoDisponibleError(f"tsa_ca_ilegible: {ruta}: {exc}") from exc
    return cas


def _cas_configuradas_tolerante() -> list[x509.Certificate]:
    """Como :func:`_cas_configuradas`, pero un PEM ilegible no tumba a quien solo ANOTA (el
    vencimiento de la cadena): la renovación, que sí necesita la CA, falla por su cuenta."""
    try:
        return _cas_configuradas()
    except RevocacionTsaNoDisponibleError:
        return []


def _urls_crl_de(cert: x509.Certificate) -> list[str]:
    try:
        dps = cert.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    except x509.ExtensionNotFound:
        return []
    return [
        n.value
        for dp in dps
        for n in (dp.full_name or [])
        if isinstance(n, x509.UniformResourceIdentifier) and n.value.lower().startswith(("http://", "https://"))
    ]


def fuente_revocacion_de(cert: x509.Certificate) -> str:
    """Qué revocación ANUNCIA el certificado de una TSA, leído de sus extensiones (sin red):
    ``autofirmada`` | ``crl`` | ``ocsp`` | ``crl_ocsp`` | ``ninguna``. Es lo que un administrador
    necesita saber de un sello de archivo: si llegado el momento de renovarlo habrá de dónde sacar
    la revocación de su TSA. Con ``tsa_ocsp_enabled=False`` el OCSP no se cuenta."""
    if cert.issuer == cert.subject:
        return FUENTE_AUTOFIRMADA
    crl = bool(_urls_crl_de(cert))
    ocsp_ok = settings.tsa_ocsp_enabled and bool(ocsp.urls_ocsp_de(cert))
    if crl and ocsp_ok:
        return FUENTE_CRL_OCSP
    if crl:
        return FUENTE_CRL
    if ocsp_ok:
        return FUENTE_OCSP
    return FUENTE_NINGUNA


def cadena_not_after(
    cert: x509.Certificate, candidatas: list[x509.Certificate] | None = None
) -> datetime | None:
    """El ``notAfter`` MÁS cercano de las CA por encima de ``cert`` (``None`` si es autofirmado o no se
    encuentra ninguna). Una cadena de sellos vale mientras viva todo el camino hasta el ancla: una CA que
    caduca antes que la hoja rompe la prueba igual que la hoja (C-6, H7). Las CA se buscan por sujeto entre
    ``candidatas`` (las del token y las del ``TimeStampValidationData``) y ``tsa_ca_pem_paths``."""
    if cert.issuer == cert.subject:
        return None
    pool = [*(candidatas or []), *_cas_configuradas_tolerante()]
    minimo: datetime | None = None
    actual = cert
    for _ in range(8):  # la cadena de una TSA no es más larga; el tope evita ciclos
        if actual.issuer == actual.subject:
            break
        emisor = next((c for c in pool if c.subject == actual.issuer and c is not actual), None)
        if emisor is None:
            break
        vence = emisor.not_valid_after_utc
        minimo = vence if minimo is None else min(minimo, vence)
        actual = emisor
    return minimo


def _crl_de_la_tsa(
    cert: x509.Certificate, ca: x509.Certificate, urls: list[str], no_anterior_a: datetime | None
) -> bytes:
    """La CRL (DER) de la CA emisora, verificada; ``TsaRevocadaError`` si ``cert`` figura en ella."""
    ultimo: Exception | None = None
    for url in urls:
        try:
            with httpx.Client(timeout=settings.tsa_timeout_seconds, follow_redirects=False) as client:
                resp = client.get(url)
            resp.raise_for_status()
            der = resp.content
            if len(der) > _CRL_MAX_BYTES:
                raise ValueError("crl_demasiado_grande")
            crl = x509.load_der_x509_crl(der)
            if crl.issuer != cert.issuer:
                raise ValueError("crl_de_otro_emisor")
            if crl.next_update_utc is not None and crl.next_update_utc < datetime.now(UTC):
                raise ValueError("crl_vencida")
            if not crl.is_signature_valid(ca.public_key()):  # type: ignore[arg-type]
                raise ValueError("crl_firma_invalida")
        except Exception as exc:  # noqa: BLE001 — cada fallo cuenta como «no disponible»
            ultimo = exc
            continue
        entrada = crl.get_revoked_certificate_by_serial_number(cert.serial_number)
        if entrada is not None and (no_anterior_a is None or entrada.revocation_date_utc <= no_anterior_a):
            raise TsaRevocadaError(f"tsa_revocada: serie {cert.serial_number:x}")
        if no_anterior_a is not None and crl.last_update_utc < no_anterior_a:
            # Una CRL emitida ANTES del sello cuyo certificado cubre no dice nada de lo ocurrido
            # después: un validador tercero no la daría por buena. (Se mira tras la revocación: una
            # CRL vieja que YA lista el certificado como revocado basta para no renovar.)
            ultimo = ValueError("crl_anterior_al_sello_previo")
            continue
        return der
    raise RevocacionTsaNoDisponibleError(f"crl_tsa_no_obtenida: {ultimo}")


def _ocsp_de_la_tsa(
    cert: x509.Certificate, ca: x509.Certificate, urls: list[str], no_anterior_a: datetime | None
) -> bytes:
    """La respuesta OCSP (DER) sobre ``cert``, verificada; ``TsaRevocadaError`` si dice REVOKED."""
    ultimo: Exception | None = None
    for url in urls:
        try:
            peticion, nonce = ocsp.build_ocsp_request_tsa(cert, ca)
            with httpx.Client(timeout=settings.tsa_timeout_seconds, follow_redirects=False) as client:
                resp = client.post(
                    url, content=peticion, headers={"Content-Type": "application/ocsp-request"}
                )
            resp.raise_for_status()
            der = resp.content
            if len(der) > ocsp.OCSP_TSA_MAX_BYTES:
                raise ValueError("ocsp_demasiado_grande")
            resultado = ocsp.verify_ocsp_tsa(der, cert, ca, nonce=nonce)
            if not resultado.valido:
                raise ValueError(resultado.motivo or "ocsp_invalida")
            fuera = catalogo.no_vigentes(resultado.algoritmos)
            if fuera:
                raise ValueError(f"algoritmo_no_vigente: {fuera[0]}")
        except Exception as exc:  # noqa: BLE001 — cada fallo cuenta como «no disponible»
            ultimo = exc
            continue
        if resultado.estado == "revoked" and (
            no_anterior_a is None
            or resultado.revocation_time is None
            or resultado.revocation_time <= no_anterior_a
        ):
            raise TsaRevocadaError(f"tsa_revocada_ocsp: serie {cert.serial_number:x}")
        if no_anterior_a is not None and (
            resultado.produced_at is None or resultado.produced_at < no_anterior_a
        ):
            ultimo = ValueError("ocsp_anterior_al_sello_previo")
            continue
        return der
    raise RevocacionTsaNoDisponibleError(f"ocsp_tsa_no_obtenida: {ultimo}")


def revocacion_de_tsa(
    cert: x509.Certificate,
    cadena: list[x509.Certificate] | None = None,
    *,
    no_anterior_a: datetime | None = None,
) -> RevocacionTsa:
    """Revocación del certificado de la TSA del sello anterior, para el ``TimeStampValidationData``.

    - Certificado AUTOFIRMADO (la TSA local de un despliegue): es su propia ancla de confianza;
      no existe CA ni revocación. ``fuente='autofirmada'`` y el ADR-034 §5 declara que ese
      ``TimeStampValidationData`` va sin ``RevocationValues``.
    - Emitido por una CA: se exige poder VERIFICAR la CA (la del token o una de ``tsa_ca_pem_paths``;
      sin CA no se acepta nada sin verificar). Orden de preferencia, para no cambiar lo que ya
      funcionaba: CRL del punto de distribución (``http(s)``); si no hay o falla, OCSP del AIA
      (``tsa_ocsp_enabled``). La CRL se exige del MISMO emisor, vigente y con firma verificada; el OCSP
      lo valida :func:`app.core.ocsp.verify_ocsp_tsa` (nonce, frescura, firmante, algoritmos).
    - ``no_anterior_a`` (el ``genTime`` del sello cuyo certificado se cubre): la evidencia tiene que ser
      posterior a él, o un validador tercero la rechazaría y se habría renovado en vano. Una revocación
      con fecha POSTERIOR al sello no lo invalida (``TsaRevocadaError`` solo si fue antes).
    - Ni CRL ni OCSP utilizable: ``RevocacionTsaNoDisponibleError`` (fail-closed: no se renueva)."""
    if cert.issuer == cert.subject:
        return RevocacionTsa(FUENTE_AUTOFIRMADA)
    urls_crl = _urls_crl_de(cert)
    urls_ocsp = ocsp.urls_ocsp_de(cert) if settings.tsa_ocsp_enabled else []
    if not urls_crl and not urls_ocsp:
        raise RevocacionTsaNoDisponibleError(
            "tsa_cert_sin_crl_distribution_point" if not ocsp.urls_ocsp_de(cert) else "tsa_ocsp_deshabilitado"
        )
    ca = next((c for c in [*(cadena or []), *_cas_configuradas()] if c.subject == cert.issuer), None)
    if ca is None:
        raise RevocacionTsaNoDisponibleError("ca_de_la_tsa_no_disponible")
    fallo_crl: RevocacionTsaNoDisponibleError | None = None
    if urls_crl:
        try:
            return RevocacionTsa(
                FUENTE_CRL, crl_der=_crl_de_la_tsa(cert, ca, urls_crl, no_anterior_a), cadena=(ca,)
            )
        except RevocacionTsaNoDisponibleError as exc:
            if not urls_ocsp:
                raise
            fallo_crl = exc
    try:
        return RevocacionTsa(
            FUENTE_OCSP, ocsp_der=_ocsp_de_la_tsa(cert, ca, urls_ocsp, no_anterior_a), cadena=(ca,)
        )
    except RevocacionTsaNoDisponibleError as exc:
        if fallo_crl is not None:
            raise RevocacionTsaNoDisponibleError(f"{fallo_crl}; {exc}") from exc
        raise


def cert_tsa_vigente() -> x509.Certificate | None:
    """Certificado de la TSA con que se sellaría AHORA, si se puede saber sin pedir un sello.

    La TSA local lo conoce (es el par montado en el despliegue); una TSA remota no: devuelve
    ``None`` y el llamante comprueba el plazo con el token ya obtenido."""
    if settings.tsa_url:
        return None
    try:
        tsa_ref = load_tsa_seal()
    except TsaSealNotFoundError as exc:
        raise TsaUnavailableError(str(exc)) from exc
    return x509.load_pem_x509_certificate(tsa_ref.cert_pem)


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
    conoce XML, solo bytes a sellar. Despacha a la TSA local o al cliente RFC 3161
    remoto (``_request_remote_timestamp``) según ``settings.tsa_url`` (ver docstring del módulo).

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
    signer_cert: x509.Certificate | None = None,
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
      6. (``signer_cert``, AREA-04-08) El ``genTime`` cae dentro de la vigencia
         del certificado FIRMANTE: es lo que atestigua que la firma existió
         mientras ese certificado era válido (el cruce que XAdES-T promete).
         Se pasa solo para el ``SignatureTimeStamp``; el ``ArchiveTimeStamp``
         se emite a propósito DESPUÉS de que el certificado caduque.
      7. (RFC 5035, AREA-04-08) El atributo firmado ``signing-certificate-v2``
         (o ``signing-certificate``, v1) nombra el certificado de la TSA con que
         se verificó. Si está, su hash TIENE que coincidir. Si falta, se rechaza
         solo si el ``genTime`` es posterior a ``settings.tsa_esscertid_corte``; antes
         del corte el sello vale y el resultado lleva la advertencia
         ``tsa_sin_esscertid_anterior_al_corte`` (los tokens de la TSA local
         anteriores a AREA-04-08 no lo llevan).

    NO valida cadena de confianza contra una CA raíz (misma postura que
    ``verify_xades``: sin CA acreditada ONAC, no hay cadena que validar) —
    por eso el llamador (``app.providers.local``) nunca sube ``acreditado`` a
    ``True`` a partir de este resultado.
    """
    try:
        return _verify_timestamp_token_inner(
            token_der,
            signature_value_c14n,
            expected_tsa_fingerprint=expected_tsa_fingerprint,
            signer_cert=signer_cert,
        )
    except Exception as exc:  # noqa: BLE001 — cualquier fallo == sello inválido (fail-closed)
        return TimestampVerifyOutcome(False, motivo=f"sello_tiempo_invalido: {exc}")


def _ess_cert_hash(attr: cms.CMSAttribute) -> tuple[str, bytes] | None:
    """(algoritmo, hash) del primer ``ESSCertID[v2]`` de un atributo
    ``signing-certificate[-v2]`` (RFC 5035), o ``None`` si no se puede leer."""
    kind = attr["type"].native
    certs = attr["values"][0]["certs"]
    if len(certs) == 0:
        return None
    first = certs[0]
    if kind == "signing_certificate_v2":
        # RFC 5035: el algoritmo por defecto es SHA-256 y puede venir omitido.
        algo = first["hash_algorithm"]["algorithm"].native if first["hash_algorithm"].contents else "sha256"
    else:  # signing_certificate (v1) — siempre SHA-1
        algo = "sha1"
    return algo, first["cert_hash"].native


def _verify_timestamp_token_inner(
    token_der: bytes,
    signature_value_c14n: bytes,
    *,
    expected_tsa_fingerprint: str | None,
    signer_cert: x509.Certificate | None = None,
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
    # El certificado que FIRMÓ el token (por el `sid`), no «el primero» del SET OF: una TSA con
    # cadena puede entregar la CA delante y el orden de un SET OF no está garantizado.
    tsa_cert = certificado_tsa_de_token(token_der)
    if tsa_cert is None:
        return TimestampVerifyOutcome(False, motivo="token_sin_certificado_tsa")

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

    # (6) El genTime dentro de la vigencia del certificado FIRMANTE (XAdES-T:
    # el sello atestigua que la firma existió mientras ese certificado era
    # válido). Antes quedaba como roadmap porque esta función no recibía el
    # certificado; ahora lo pasa el llamador (`verify_xades`) para el
    # `SignatureTimeStamp`. Sin `signer_cert` (el `ArchiveTimeStamp`, emitido a
    # propósito después de que el certificado caduque) no se aplica.
    if signer_cert is not None and not (
        signer_cert.not_valid_before_utc <= gen_time <= signer_cert.not_valid_after_utc
    ):
        return TimestampVerifyOutcome(False, motivo="gentime_fuera_de_vigencia_firmante")

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
    ess_cert_id: tuple[str, bytes] | None = None
    for attr in fresh_attrs:
        if attr["type"].native == "content_type":
            content_type_attr = attr["values"][0].native
        elif attr["type"].native == "message_digest":
            message_digest_attr = attr["values"][0].native
        elif attr["type"].native in ("signing_certificate_v2", "signing_certificate"):
            ess_cert_id = _ess_cert_hash(attr)

    if content_type_attr != "tst_info":
        return TimestampVerifyOutcome(False, motivo="signed_attrs_content_type_no_coincide")
    if message_digest_attr != hashlib.sha256(tst_info_der).digest():
        return TimestampVerifyOutcome(False, motivo="signed_attrs_message_digest_no_coincide")

    # (7) ESSCertID — liga el token al certificado de la TSA concreto. Va dentro
    # de los `signedAttrs`, que la firma CMS de más abajo cubre: quitarlo o
    # cambiarlo rompe esa firma.
    advertencias: list[str] = []
    if ess_cert_id is None:
        # Corte por `genTime` (no por la fecha de verificar): lo que importa es
        # cuándo selló la TSA. Desde el corte, un token sin ESSCertID no es de
        # un corpus anterior, es un token defectuoso; antes, es historia que
        # sigue valiendo y que el verificador declara en lugar de callar.
        if gen_time >= settings.tsa_esscertid_corte:
            return TimestampVerifyOutcome(False, motivo="tsa_sin_esscertid")
        advertencias.append(ADVERTENCIA_SIN_ESSCERTID)
    else:
        algo, esperado = ess_cert_id
        if algo not in ("sha256", "sha1"):
            return TimestampVerifyOutcome(False, motivo="esscertid_algoritmo_no_soportado")
        cert_der = tsa_cert.public_bytes(serialization.Encoding.DER)
        actual = hashlib.sha256(cert_der).digest() if algo == "sha256" else hashlib.sha1(cert_der).digest()  # noqa: S324 — ESSCertID v1 (RFC 5035) lo fija en SHA-1; solo se COMPARA
        if actual != esperado:
            return TimestampVerifyOutcome(False, motivo="esscertid_no_coincide")

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

    return TimestampVerifyOutcome(
        True,
        gen_time=gen_time,
        motivo=None,
        advertencias=advertencias,
        tsa_cert_not_after=tsa_cert.not_valid_after_utc,
        algoritmos=(
            signer_info["digest_algorithm"]["algorithm"].native,
            catalogo.identificador_de_clave(tsa_public_key),
        ),
    )
