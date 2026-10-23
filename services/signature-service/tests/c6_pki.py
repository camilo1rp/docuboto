# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Material de prueba de C-6: una CA, una TSA emitida por ella con CRL y/o AIA OCSP, un respondedor
OCSP (el de la CA o un delegado) y una respuesta OCSP ARMADA A MANO con asn1crypto.

A mano porque ``OCSPResponseBuilder`` solo admite UNA ``SingleResponse``, firma siempre con el
``producedAt`` de ahora y no deja poner un nonce arbitrario: justo lo que hay que ejercitar. La
corrección del armado la prueba un control positivo (una respuesta bien formada VALIDA), no la fe.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from asn1crypto import core
from asn1crypto import ocsp as a_ocsp
from asn1crypto import x509 as a_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509 import ocsp as c_ocsp
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtendedKeyUsageOID, NameOID

from app.providers.base import SignerRef

URL_OCSP = "http://ocsp.c6.test/tsa"
URL_CRL = "http://crl.c6.test/tsa.crl"


def pem_cert(c: x509.Certificate) -> bytes:
    return c.public_bytes(serialization.Encoding.PEM)


def pem_key(k: rsa.RSAPrivateKey) -> bytes:
    return k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _nombre(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def ca_nueva(cn: str = "ca-c6", *, dias: int = 3650, hasta: datetime | None = None):  # noqa: ANN201
    """(clave, certificado) de una CA autofirmada. ``hasta`` fija su ``notAfter`` (p. ej. ya vencida)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_nombre(cn)).issuer_name(_nombre(cn)).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=5000))
        .not_valid_after(hasta or ahora + timedelta(days=dias))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def tsa_emitida(  # noqa: ANN201
    ca, *, cn: str = "tsa-c6", dias: int = 365, cdp: str | None = None, ocsp_url: str | None = None
):
    """(clave, certificado) de una TSA emitida por ``ca = (clave, cert)``, con las URL que se pidan."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(UTC)
    b = (
        x509.CertificateBuilder()
        .subject_name(_nombre(cn)).issuer_name(ca[1].subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1)).not_valid_after(ahora + timedelta(days=dias))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    )
    if cdp:
        b = b.add_extension(
            x509.CRLDistributionPoints([
                x509.DistributionPoint(
                    full_name=[x509.UniformResourceIdentifier(cdp)], relative_name=None,
                    reasons=None, crl_issuer=None,
                )
            ]),
            critical=False,
        )
    if ocsp_url:
        b = b.add_extension(
            x509.AuthorityInformationAccess([
                x509.AccessDescription(
                    AuthorityInformationAccessOID.OCSP, x509.UniformResourceIdentifier(ocsp_url)
                )
            ]),
            critical=False,
        )
    return key, b.sign(ca[0], hashes.SHA256())


def instalar_tsa(monkeypatch, key, cert) -> None:  # noqa: ANN001
    """La TSA con que se sellará de ahora en adelante."""
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=pem_cert(cert), key_pem=pem_key(key), key_password=None),
    )


def declarar_ca(monkeypatch, tmp_path, *cas) -> None:  # noqa: ANN001, ANN002
    """Las CA de la TSA por configuración (el token de la TSA de pruebas no las trae)."""
    from app.core import tsa

    f = tmp_path / "tsa-ca.pem"
    f.write_bytes(b"".join(pem_cert(c[1]) for c in cas))
    monkeypatch.setattr(tsa.settings, "tsa_ca_pem_paths", str(f))


def delegado_ocsp(ca, *, eku: bool = True, nocheck: bool = True, dias: int = 30):  # noqa: ANN201
    """(clave, certificado) de un respondedor delegado emitido por ``ca``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(UTC)
    b = (
        x509.CertificateBuilder()
        .subject_name(_nombre("ocsp-delegado-c6")).issuer_name(ca[1].subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=10)).not_valid_after(ahora + timedelta(days=dias))
    )
    if eku:
        b = b.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.OCSP_SIGNING]), critical=False)
    if nocheck:
        b = b.add_extension(x509.OCSPNoCheck(), critical=False)
    return key, b.sign(ca[0], hashes.SHA256())


def crl_de(  # noqa: ANN201
    ca, *, last_update: datetime | None = None, revocados: tuple[tuple[int, datetime], ...] = (),
    dias: int = 7,
) -> bytes:
    ahora = datetime.now(UTC)
    b = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca[1].subject)
        .last_update(last_update or ahora)
        .next_update(ahora + timedelta(days=dias))
    )
    for serie, cuando in revocados:
        b = b.add_revoked_certificate(
            x509.RevokedCertificateBuilder().serial_number(serie).revocation_date(cuando).build()
        )
    return b.sign(ca[0], hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def _s(instante: datetime) -> datetime:
    """OCSP usa GeneralizedTime sin fracciones (RFC 6960 §4.2.2.1)."""
    return instante.replace(microsecond=0)


def _certid(cert: x509.Certificate, ca_cert: x509.Certificate, hash_certid: str):  # noqa: ANN202
    alg = {"sha1": hashes.SHA1(), "sha256": hashes.SHA256()}[hash_certid]
    r = c_ocsp.OCSPRequestBuilder().add_certificate(cert, ca_cert, alg).build()
    return a_ocsp.CertId({
        "hash_algorithm": {"algorithm": hash_certid},
        "issuer_name_hash": r.issuer_name_hash,
        "issuer_key_hash": r.issuer_key_hash,
        "serial_number": r.serial_number,
    })


def respuesta_ocsp(  # noqa: ANN201
    cert: x509.Certificate,
    ca,  # noqa: ANN001  (clave, cert) de la CA emisora de ``cert``
    *,
    firmante=None,  # noqa: ANN001  (clave, cert) de quien firma; None = la propia CA
    incluir_cert_firmante: bool = False,
    estado: str = "good",
    revocado_en: datetime | None = None,
    nonce: bytes | None = None,
    produced_at: datetime | None = None,
    this_update: datetime | None = None,
    next_update: datetime | None = None,
    hash_firma=None,  # noqa: ANN001
    hash_certid: str = "sha256",
    responder_por: str = "key",
    antes: tuple[x509.Certificate, ...] = (),
    status_respuesta: str = "successful",
) -> bytes:
    """``BasicOCSPResponse`` armada a mano. ``antes``: certificados cuyas ``SingleResponse`` (``good``)
    se anteponen a la buena, para probar que se BUSCA la del certificado preguntado."""
    ahora = datetime.now(UTC)
    clave, cert_firmante = firmante if firmante is not None else ca
    hash_firma = hash_firma or hashes.SHA256()

    def _single(c: x509.Certificate, est: str, rev: datetime | None) -> a_ocsp.SingleResponse:
        if est == "good":
            status = a_ocsp.CertStatus(name="good", value=core.Null())
        elif est == "unknown":
            status = a_ocsp.CertStatus(name="unknown", value=core.Null())
        else:
            status = a_ocsp.CertStatus(
                name="revoked",
                value=a_ocsp.RevokedInfo({"revocation_time": _s(rev or ahora - timedelta(days=1))}),
            )
        return a_ocsp.SingleResponse({
            "cert_id": _certid(c, ca[1], hash_certid),
            "cert_status": status,
            "this_update": _s(this_update or ahora - timedelta(seconds=30)),
            "next_update": _s(next_update if next_update is not None else ahora + timedelta(days=1)),
        })

    respuestas = [_single(c, "good", None) for c in antes] + [_single(cert, estado, revocado_en)]
    if responder_por == "key":
        rid = a_ocsp.ResponderId(
            name="by_key",
            value=x509.SubjectKeyIdentifier.from_public_key(cert_firmante.public_key()).digest,
        )
    else:
        rid = a_ocsp.ResponderId(name="by_name", value=a_x509.Name.load(cert_firmante.subject.public_bytes()))
    datos = {
        "responder_id": rid,
        "produced_at": _s(produced_at or ahora),
        "responses": respuestas,
    }
    if nonce is not None:
        datos["response_extensions"] = [{"extn_id": "nonce", "critical": False, "extn_value": core.OctetString(nonce)}]
    tbs = a_ocsp.ResponseData(datos)
    firma = clave.sign(tbs.dump(), padding.PKCS1v15(), hash_firma)
    basica = {
        "tbs_response_data": tbs,
        "signature_algorithm": {"algorithm": f"{hash_firma.name}_rsa"},
        "signature": firma,
    }
    if incluir_cert_firmante:
        basica["certs"] = [a_x509.Certificate.load(cert_firmante.public_bytes(serialization.Encoding.DER))]
    envoltura: dict = {"response_status": status_respuesta}
    if status_respuesta == "successful":
        envoltura["response_bytes"] = {
            "response_type": "basic_ocsp_response",
            "response": basica,
        }
    return a_ocsp.OCSPResponse(envoltura).dump()


def nonce_de(peticion_der: bytes) -> bytes | None:
    ext = c_ocsp.load_der_ocsp_request(peticion_der).extensions
    try:
        return ext.get_extension_for_class(x509.OCSPNonce).value.nonce
    except x509.ExtensionNotFound:
        return None


class Pedidas:
    """Registro de lo que el código pidió por red (URL y método)."""

    def __init__(self) -> None:
        self.get: list[str] = []
        self.post: list[tuple[str, bytes]] = []


def servir(  # noqa: ANN201
    monkeypatch,  # noqa: ANN001
    *,
    crl: bytes | Exception | None = None,
    ocsp: Callable[[bytes], bytes | Exception] | bytes | Exception | None = None,
):
    """Sustituye ``httpx.Client`` de ``app.core.tsa``: ``get`` sirve ``crl``; ``post`` llama a ``ocsp``
    con la petición DER (o devuelve los bytes/lanza la excepción)."""
    from app.core import tsa

    pedidas = Pedidas()

    class _Cliente:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a) -> None:  # noqa: ANN002
            return None

        def get(self, url: str):  # noqa: ANN202
            pedidas.get.append(url)
            if crl is None:
                raise httpx.ConnectError("sin CRL en esta prueba")
            if isinstance(crl, Exception):
                raise crl
            return httpx.Response(200, content=crl, request=httpx.Request("GET", url))

        def post(self, url: str, *, content: bytes, headers: dict):  # noqa: ANN202
            assert headers["Content-Type"] == "application/ocsp-request"
            pedidas.post.append((url, content))
            if ocsp is None:
                raise httpx.ConnectError("sin OCSP en esta prueba")
            r = ocsp(content) if callable(ocsp) else ocsp
            if isinstance(r, Exception):
                raise r
            return httpx.Response(200, content=r, request=httpx.Request("POST", url))

    monkeypatch.setattr(tsa.httpx, "Client", _Cliente)
    return pedidas


def hex_sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
