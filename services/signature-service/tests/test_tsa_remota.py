# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente RFC 3161 contra una TSA REMOTA (E06, ADR-016 addendum).

Por qué existe: el sello de tiempo **acreditado** no lo puede emitir una TSA
local por buena que sea — lo emite el prestador que la entidad contrata. Con
este camino en forma de stub, una instalación con TSA acreditada contratada no
tenía dónde enchufarla, y la acreditación quedaba declarada "propia de la
instalación" sobre una instalación que no podía instalarla.

La TSA remota se dobla construyendo respuestas RFC 3161 **de verdad** —DER
completo, firmado, con su nonce— y no devolviendo objetos de mentira: lo que se
comprueba aquí es precisamente el parseo, la comparación del nonce y la
verificación del token, y un doble que devolviera un `TimestampToken` ya hecho
no ejercitaría nada de eso. Es la misma lección del mock que inventa la clave.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from asn1crypto import algos, cms, core, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.core import tsa as tsa_module
from app.core.config import settings
from app.core.tsa import TsaUnavailableError, sello_acreditado

URL = "https://tsa.operador.test/timestamp"
SELLADO = b"bytes-que-se-quieren-sellar"


def _par_tsa(*, eku_critical: bool = True) -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tsa-operador-acreditado")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=eku_critical
        )
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )


def _token_der(
    *, imprint: bytes, nonce: int | None, cert_pem: bytes, key_pem: bytes, con_cert: bool = True,
    con_esscertid: bool = True,
) -> bytes:
    """Un `TimeStampToken` CMS real, firmado, como el que devuelve una TSA."""
    cert = x509.load_pem_x509_certificate(cert_pem)
    key = serialization.load_pem_private_key(key_pem, password=None)
    gen_time = datetime.now(UTC)

    campos = {
        "version": 1,
        "policy": "1.2.3.4.5.6.7.8.9.2",
        "message_imprint": tsp.MessageImprint(
            {
                "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                "hashed_message": imprint,
            }
        ),
        "serial_number": 42,
        "gen_time": gen_time,
        "accuracy": tsp.Accuracy({"seconds": 1}),
        "ordering": False,
    }
    if nonce is not None:
        campos["nonce"] = nonce
    tst_info_der = tsp.TSTInfo(campos).dump()

    asn1_cert_ess = asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    atributos = [
        cms.CMSAttribute(
            {
                "type": "content_type",
                "values": cms.SetOfContentType([cms.ContentType("tst_info")]),
            }
        ),
        cms.CMSAttribute(
            {
                "type": "message_digest",
                "values": cms.SetOfOctetString(
                    [core.OctetString(hashlib.sha256(tst_info_der).digest())]
                ),
            }
        ),
        cms.CMSAttribute(
            {"type": "signing_time", "values": cms.SetOfTime([cms.Time({"utc_time": gen_time})])}
        ),
    ]
    if con_esscertid:
        # Una TSA conforme (RFC 3161 §2.4.2 / RFC 5035) lo lleva SIEMPRE: desde la
        # fecha de corte `tsa_esscertid_corte` un token sin él se rechaza.
        atributos.append(
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
                                                "hash_algorithm": algos.DigestAlgorithm(
                                                    {"algorithm": "sha256"}
                                                ),
                                                "cert_hash": hashlib.sha256(
                                                    asn1_cert_ess.dump()
                                                ).digest(),
                                            }
                                        )
                                    ]
                                }
                            )
                        ]
                    ),
                }
            )
        )
    signed_attrs = cms.CMSAttributes(atributos)
    firma = key.sign(signed_attrs.dump(), padding.PKCS1v15(), hashes.SHA256())

    asn1_cert = asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    signer_info = cms.SignerInfo(
        {
            "version": "v1",
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
            "signature": firma,
        }
    )
    signed_data = {
        "version": "v3",
        "digest_algorithms": cms.DigestAlgorithms(
            [algos.DigestAlgorithm({"algorithm": "sha256"})]
        ),
        "encap_content_info": cms.EncapsulatedContentInfo(
            {"content_type": "tst_info", "content": core.ParsableOctetString(tst_info_der)}
        ),
        "signer_infos": cms.SignerInfos([signer_info]),
    }
    if con_cert:
        signed_data["certificates"] = cms.CertificateSet([cms.CertificateChoices(name="certificate", value=asn1_cert)])
    return cms.ContentInfo(
        {"content_type": "signed_data", "content": cms.SignedData(signed_data)}
    ).dump()


class _RespSoloEstado(core.Sequence):
    """`TimeStampResp` SIN `timeStampToken`, que es lo que devuelve un rechazo.

    Hay que declararla a mano porque `asn1crypto.tsp.TimeStampResp` marca el
    token como OBLIGATORIO, cuando la RFC 3161 §2.4.2 lo hace opcional. Ese
    detalle no es del test: es lo que hacía que el cliente perdiera el
    `failInfo` de un rechazo real, y por eso el cliente abre la respuesta como
    secuencia genérica.
    """

    _fields = [("status", tsp.PKIStatusInfo)]


def _respuesta(token_der: bytes | None, *, estado: str = "granted", fail_info: str | None = None) -> bytes:
    campos: dict = {"status": estado}
    if fail_info is not None:
        # `PKIFailureInfo` es un BIT STRING: se construye con el CONJUNTO de
        # banderas activas, no con una cadena suelta.
        campos["fail_info"] = {fail_info}
    estado_info = tsp.PKIStatusInfo(campos)
    if token_der is None:
        return _RespSoloEstado({"status": estado_info}).dump()
    return tsp.TimeStampResp(
        {"status": estado_info, "time_stamp_token": cms.ContentInfo.load(token_der)}
    ).dump()


def _doblar_post(monkeypatch, responder) -> list[bytes]:
    """Sustituye `httpx.post` por `responder(peticion_der) -> httpx.Response`."""
    enviadas: list[bytes] = []

    def _post(url, *, content, headers, timeout):  # noqa: ANN001
        assert url == URL
        assert headers["Content-Type"] == "application/timestamp-query"
        enviadas.append(content)
        return responder(content)

    monkeypatch.setattr(tsa_module.httpx, "post", _post)
    return enviadas


@pytest.fixture(autouse=True)
def _tsa_remota(monkeypatch):
    monkeypatch.setattr(settings, "tsa_url", URL)


def test_un_sello_remoto_valido_se_acepta(monkeypatch) -> None:
    """El camino feliz, con DER real de punta a punta."""
    cert_pem, key_pem = _par_tsa()

    def responder(peticion_der: bytes) -> httpx.Response:
        pedido = tsp.TimeStampReq.load(peticion_der)
        # La TSA responde al nonce que le pidieron, como manda la RFC.
        token = _token_der(
            imprint=pedido["message_imprint"]["hashed_message"].native,
            nonce=pedido["nonce"].native,
            cert_pem=cert_pem,
            key_pem=key_pem,
        )
        return httpx.Response(200, content=_respuesta(token))

    enviadas = _doblar_post(monkeypatch, responder)
    token = tsa_module.build_timestamp_token(SELLADO)

    assert token.token_der
    assert token.tsa_cert_fingerprint == x509.load_pem_x509_certificate(cert_pem).fingerprint(
        hashes.SHA256()
    ).hex()

    # `certReq` va en la petición: sin él la respuesta puede no traer el
    # certificado, y entonces el token no se puede verificar de forma autónoma
    # dentro de diez años, que es justo para lo que existe.
    pedido = tsp.TimeStampReq.load(enviadas[0])
    assert pedido["cert_req"].native is True
    assert pedido["nonce"].native is not None
    assert pedido["message_imprint"]["hashed_message"].native == hashlib.sha256(SELLADO).digest()


def test_un_nonce_que_no_coincide_se_rechaza(monkeypatch) -> None:
    """Anti-reproducción (RFC 3161 §2.4.1).

    Sin comparar el nonce, nada distingue una respuesta fresca de la
    REPRODUCCIÓN de un token antiguo capturado en la red — y la fecha cierta
    que se anotaría sería la de otro acto, en un registro que existe
    precisamente para dar fe de cuándo ocurrió éste.
    """
    cert_pem, key_pem = _par_tsa()

    def responder(peticion_der: bytes) -> httpx.Response:
        pedido = tsp.TimeStampReq.load(peticion_der)
        token = _token_der(
            imprint=pedido["message_imprint"]["hashed_message"].native,
            nonce=pedido["nonce"].native + 1,  # otro nonce
            cert_pem=cert_pem,
            key_pem=key_pem,
        )
        return httpx.Response(200, content=_respuesta(token))

    _doblar_post(monkeypatch, responder)
    with pytest.raises(TsaUnavailableError, match="nonce_no_coincide"):
        tsa_module.build_timestamp_token(SELLADO)


def test_un_token_sobre_otros_bytes_se_rechaza(monkeypatch) -> None:
    """El token se verifica ENTERO antes de guardarlo.

    Un sello sobre un imprint distinto no ata nada al acto que se está
    firmando. Descubrirlo al verificar, meses después, sería descubrirlo cuando
    ya no se puede corregir.
    """
    cert_pem, key_pem = _par_tsa()

    def responder(peticion_der: bytes) -> httpx.Response:
        pedido = tsp.TimeStampReq.load(peticion_der)
        token = _token_der(
            imprint=hashlib.sha256(b"otra-cosa").digest(),
            nonce=pedido["nonce"].native,
            cert_pem=cert_pem,
            key_pem=key_pem,
        )
        return httpx.Response(200, content=_respuesta(token))

    _doblar_post(monkeypatch, responder)
    with pytest.raises(TsaUnavailableError, match="token_invalido"):
        tsa_module.build_timestamp_token(SELLADO)


def test_un_certificado_sin_eku_critico_se_rechaza(monkeypatch) -> None:
    """Mismo requisito que para la TSA local (RFC 3161 §2.3), venga de donde venga."""
    cert_pem, key_pem = _par_tsa(eku_critical=False)

    def responder(peticion_der: bytes) -> httpx.Response:
        pedido = tsp.TimeStampReq.load(peticion_der)
        token = _token_der(
            imprint=pedido["message_imprint"]["hashed_message"].native,
            nonce=pedido["nonce"].native,
            cert_pem=cert_pem,
            key_pem=key_pem,
        )
        return httpx.Response(200, content=_respuesta(token))

    _doblar_post(monkeypatch, responder)
    with pytest.raises(TsaUnavailableError, match="token_invalido"):
        tsa_module.build_timestamp_token(SELLADO)


def test_un_token_sin_certificado_se_rechaza(monkeypatch) -> None:
    """Sin el certificado embebido el token es inverificable más tarde."""
    cert_pem, key_pem = _par_tsa()

    def responder(peticion_der: bytes) -> httpx.Response:
        pedido = tsp.TimeStampReq.load(peticion_der)
        token = _token_der(
            imprint=pedido["message_imprint"]["hashed_message"].native,
            nonce=pedido["nonce"].native,
            cert_pem=cert_pem,
            key_pem=key_pem,
            con_cert=False,
        )
        return httpx.Response(200, content=_respuesta(token))

    _doblar_post(monkeypatch, responder)
    with pytest.raises(TsaUnavailableError, match="sin_certificado"):
        tsa_module.build_timestamp_token(SELLADO)


def test_un_rechazo_de_la_tsa_dice_por_que(monkeypatch) -> None:
    """`failInfo` es lo único que permite a quien la configuró saber qué arreglar."""
    _doblar_post(
        monkeypatch,
        lambda _p: httpx.Response(
            200, content=_respuesta(None, estado="rejection", fail_info="bad_alg")
        ),
    )
    with pytest.raises(TsaUnavailableError, match="bad_alg"):
        tsa_module.build_timestamp_token(SELLADO)


def test_un_error_http_no_se_confunde_con_un_rechazo(monkeypatch) -> None:
    _doblar_post(monkeypatch, lambda _p: httpx.Response(500, content=b""))
    with pytest.raises(TsaUnavailableError, match="http_500"):
        tsa_module.build_timestamp_token(SELLADO)


def test_la_tsa_inalcanzable_no_cae_a_la_local(monkeypatch) -> None:
    """Con `tsa_url` puesto, NUNCA se sella con la TSA local.

    Caer al camino local encubriría una TSA remota mal configurada: el sistema
    anotaría fecha cierta con su propio par autofirmado creyendo el operador que
    la puso el prestador acreditado que contrató.
    """

    def _revienta(_p):  # noqa: ANN001
        raise httpx.ConnectError("sin ruta al host")

    _doblar_post(monkeypatch, _revienta)
    with pytest.raises(TsaUnavailableError, match="inalcanzable"):
        tsa_module.build_timestamp_token(SELLADO)


def test_una_respuesta_ilegible_no_pasa_por_valida(monkeypatch) -> None:
    _doblar_post(monkeypatch, lambda _p: httpx.Response(200, content=b"esto no es DER"))
    with pytest.raises(TsaUnavailableError, match="ilegible"):
        tsa_module.build_timestamp_token(SELLADO)


# ---------------------------------------------------------------------------
# Declarar la TSA acreditada exige las DOS condiciones
# ---------------------------------------------------------------------------


def test_la_tsa_local_no_se_acredita_con_un_booleano(monkeypatch) -> None:
    """El acoplamiento `tsa_url` + `tsa_acreditada` ES el control.

    Una TSA local acreditada no existe: es un par autofirmado del propio
    despliegue. Si bastara el booleano, `TSA_ACREDITADA=true` en un entorno de
    pruebas haría que el sistema declarara fecha cierta oponible sobre sellos
    que emite él mismo.
    """
    monkeypatch.setattr(settings, "tsa_url", "")
    monkeypatch.setattr(settings, "tsa_acreditada", True)
    assert sello_acreditado() is False


def test_una_tsa_remota_declarada_si_se_acredita(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tsa_acreditada", True)
    assert sello_acreditado() is True


def test_una_tsa_remota_sin_declarar_no_se_acredita(monkeypatch) -> None:
    """Por defecto no se acredita nada: sub-declarar es la dirección segura."""
    monkeypatch.setattr(settings, "tsa_acreditada", False)
    assert sello_acreditado() is False


def test_un_sello_remoto_sin_esscertid_se_rechaza_desde_el_corte(monkeypatch) -> None:
    """AREA-04-09: el corte por `genTime` también protege el cliente remoto.

    Una TSA que hoy emite sellos sin `signing-certificate-v2` no es conforme; el
    token lo rechaza el propio cliente antes de embeberlo, no `/verify` años después.
    """
    cert_pem, key_pem = _par_tsa()

    def responder(peticion_der: bytes) -> httpx.Response:
        pedido = tsp.TimeStampReq.load(peticion_der)
        token = _token_der(
            imprint=pedido["message_imprint"]["hashed_message"].native,
            nonce=pedido["nonce"].native,
            cert_pem=cert_pem,
            key_pem=key_pem,
            con_esscertid=False,
        )
        return httpx.Response(200, content=_respuesta(token))

    _doblar_post(monkeypatch, responder)
    with pytest.raises(tsa_module.TsaUnavailableError, match="tsa_sin_esscertid"):
        tsa_module.build_timestamp_token(SELLADO)
