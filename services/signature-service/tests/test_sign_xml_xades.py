# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cobertura XAdES-B/-T enveloped real de /sign-xml y /verify (E06, ADR-016, Inc.1/4).

Complementa ``tests/test_signature.py`` (que cubre el flujo HMAC 'nativa'
preexistente) con el camino nuevo del Incremento 1: ``objeto_tipo="indice"``
con ``settings.signer_provider="xades_local"`` (el default) produce una firma
XAdES-B REAL vía ``app.providers.local.LocalSigner``. Desde el Incremento 4
(``settings.signer_timestamp_enabled=True``, default), esa misma firma sube a
XAdES-T con un sello de tiempo RFC 3161 de la TSA local (``app.core.tsa``) —
ver la sección "XAdES-T" más abajo para los tests dedicados.

No se toca el filesystem ni se depende de ``scripts/gen-dev-signing-cert.sh``
ni ``scripts/gen-dev-tsa-cert.sh``: cada test genera su propio par de claves
RSA-2048 efímero (``cryptography``) y parchea ``app.services.signing_service.load_seal``
(sello institucional) / ``app.core.tsa.load_tsa_seal`` (TSA) directamente — el
mismo patrón que documenta la memoria del proyecto para pruebas de este
proveedor. El upload a storage-service también se mockea
(``app.clients.storage_client.upload_signed_xml``) para no requerir un
storage-service real en la suite unitaria.

``signature_env`` instala, por defecto, TANTO un sello institucional COMO un
sello de TSA (ambos ephemeral, EKU timeStamping crítico en el de la TSA) —
así que TODOS los tests preexistentes de este archivo (Inc.1) ahora firman en
XAdES-T por defecto, reflejando el comportamiento real de producción. Los
tests que necesitan simular la AUSENCIA de TSA (kill-switch, 503) usan
``install_tsa_seal(None)`` para desinstalar el default.
"""

from __future__ import annotations

import json
import uuid
from base64 import b64decode, b64encode
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from lxml import etree

from app.clients import storage_client
from app.core import tsa as tsa_module
from app.core.config import settings
from app.core.keys import SealNotFoundError, TsaSealNotFoundError, cert_fingerprint
from app.providers.base import SignerRef
from tests.conftest import TENANT_HEADERS, USER

# ---------------------------------------------------------------------------
# Helpers — certificados efímeros (nunca tocan el filesystem ni secrets/)
# ---------------------------------------------------------------------------


def _generate_cert(
    cn: str,
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> tuple[bytes, bytes]:
    """Genera un par (cert_pem, key_pem) RSA-2048 autofirmado, efímero, en memoria."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    nb = not_before if not_before is not None else now - timedelta(days=1)
    na = not_after if not_after is not None else now + timedelta(days=365)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _signer_ref(cert_pem: bytes, key_pem: bytes) -> SignerRef:
    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=None,
        tipo_firma="sello_sistema",
        nivel_conformidad="xades_b_no_acreditado",
        acreditado=False,
    )


def _generate_tsa_cert(
    cn: str = "orpycamcp-dev-tsa",
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
    with_eku: bool = True,
    eku_critical: bool = True,
) -> tuple[bytes, bytes]:
    """Genera un par (cert_pem, key_pem) RSA-2048 autofirmado para la TSA (Inc.4).

    Por defecto lleva EKU ``id-kp-timeStamping`` CRÍTICO (requisito RFC 3161
    §2.3 — ver ``app.core.tsa._assert_timestamping_eku``). ``with_eku=False``/
    ``eku_critical=False`` producen certificados deliberadamente inválidos
    para las pruebas de rechazo.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    nb = not_before if not_before is not None else now - timedelta(days=1)
    na = not_after if not_after is not None else now + timedelta(days=365)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
    )
    if with_eku:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=eku_critical
        )
    cert = builder.sign(key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _tsa_signer_ref(cert_pem: bytes, key_pem: bytes) -> SignerRef:
    """``load_tsa_seal`` real solo llena cert_pem/key_pem/key_password (ver keys.py)."""
    return SignerRef(cert_pem=cert_pem, key_pem=key_pem, key_password=None)


def _generate_ca(cn: str = "orpycamcp-dev-ca"):
    """CA raíz efímera, en memoria (``cryptography``, sin openssl CLI ni
    filesystem — mismo espíritu que ``scripts/gen-dev-ca.sh`` pero para
    pruebas): ``basicConstraints CA:TRUE`` + ``keyUsage keyCertSign/cRLSign``.

    Devuelve ``(ca_key, ca_cert)`` — los objetos ``cryptography``, no PEM/DER,
    para que los helpers siguientes (``_issue_seal_signed_by_ca``,
    ``_build_crl``) puedan firmar con ella directamente.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _ca_cert_pem(ca_cert: x509.Certificate) -> bytes:
    return ca_cert.public_bytes(serialization.Encoding.PEM)


def _issue_seal_signed_by_ca(
    ca_key, ca_cert: x509.Certificate, cn: str = "orpycamcp-dev"
) -> tuple[bytes, bytes, x509.Certificate]:
    """Emite un sello (cert+key RSA-2048) firmado por ``ca_cert`` — NO
    autofirmado, a diferencia de ``_generate_cert``. Devuelve
    ``(seal_cert_pem, seal_key_pem, seal_cert)`` — el objeto ``Certificate``
    se expone para que los tests puedan leer su ``serial_number`` (revocación).
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .sign(ca_key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem, cert


def _build_crl(ca_key, ca_cert: x509.Certificate, *, revoked_serials: tuple[int, ...] = ()) -> bytes:
    """CRL DER firmada por ``ca_cert`` (``CertificateRevocationListBuilder``,
    en memoria) — ``revoked_serials`` vacío reproduce la CRL "0 revocados"
    honesta que produce ``scripts/gen-dev-ca.sh`` en desarrollo.
    """
    now = datetime.now(UTC)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now + timedelta(days=3650))
    )
    for serial in revoked_serials:
        revoked = x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(now).build()
        builder = builder.add_revoked_certificate(revoked)
    crl = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    return crl.public_bytes(serialization.Encoding.DER)


def _lt_signer_ref(cert_pem: bytes, key_pem: bytes, ca_cert_pem: bytes, crl_der: bytes) -> SignerRef:
    """``SignerRef`` con material LT/LTA (``ca_cert_pem``/``crl_der``) — mismo
    patrón que ``_signer_ref`` pero co-ubicando la CA/CRL como haría
    ``app.core.keys.load_seal`` con ``scripts/gen-dev-ca.sh`` real."""
    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=None,
        tipo_firma="sello_sistema",
        nivel_conformidad="xades_b_no_acreditado",
        acreditado=False,
        ca_cert_pem=ca_cert_pem,
        crl_der=crl_der,
    )


def _issue_ocsp_responder(
    ca_key, ca_cert: x509.Certificate, *, cn: str = "orpycamcp-dev-ocsp-responder"
) -> tuple[bytes, bytes]:
    """Responder OCSP DELEGADO efímero — EKU ``id-kp-OCSPSigning`` +
    ``id-pkix-ocsp-nocheck`` (RFC 6960 §4.2.2.2), emitido por ``ca_cert``.
    Mismo espíritu que ``scripts/gen-dev-ocsp-responder.sh`` pero en memoria
    (``cryptography``, sin openssl CLI ni filesystem)."""
    from cryptography.x509 import OCSPNoCheck

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=397))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.OCSP_SIGNING]), critical=False)
        .add_extension(OCSPNoCheck(), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _lt_ocsp_signer_ref(
    cert_pem: bytes,
    key_pem: bytes,
    ca_cert_pem: bytes,
    crl_der: bytes,
    ocsp_cert_pem: bytes,
    ocsp_key_pem: bytes,
) -> SignerRef:
    """``SignerRef`` con material LT/LTA Y del responder OCSP delegado —
    mismo patrón que ``_lt_signer_ref``, extendido con
    ``ocsp_cert_pem``/``ocsp_key_pem`` (co-ubicados por
    ``app.core.keys.load_seal`` en el mismo directorio del sello)."""
    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=None,
        tipo_firma="sello_sistema",
        nivel_conformidad="xades_b_no_acreditado",
        acreditado=False,
        ca_cert_pem=ca_cert_pem,
        crl_der=crl_der,
        ocsp_cert_pem=ocsp_cert_pem,
        ocsp_key_pem=ocsp_key_pem,
    )


INDICE_XML = (
    b'<indice xmlns="urn:orpyca:indice">'
    b'<expediente id="exp-1">Contenido original</expediente>'
    b"</indice>"
)


class _FakeFirmaTable:
    """Reemplaza la tabla `firma` real: INSERT ... RETURNING y SELECT ... WHERE id.

    Permite un round-trip completo firma -> verificación dentro del mismo test
    (misma conexión mockeada), reflejando de vuelta exactamente los valores que
    el router/provider calcularon de verdad (no una fila fija desconectada del
    request, a diferencia de `tests/test_signature.py::_firma_row`).
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def fetchrow(self, query: str, *args):
        q = query.strip()
        if q.startswith("INSERT INTO firma ("):
            (
                firma_id_param,
                objeto_tipo,
                objeto_id,
                formato_solicitado,
                firmante_id,
                payload_sha256,
                firma_blob,
                provider,
                sello_tiempo,
                certificado_pem,
                minio_key_firmado,
                nivel_conformidad,
                xades_level,
                tipo_firma,
                certificado_fingerprint,
                signed_xml_sha256,
                tsa_token,
                tsa_timestamp,
                tsa_cert_fingerprint,
                tsa_acreditado,
                lt_material_present,
                ca_fingerprint,
                crl_fingerprint,
                crl_this_update,
                crl_next_update,
                archive_timestamp_present,
                archive_timestamp_at,
                archive_tsa_cert_fingerprint,
                revocation_provenance,
                archive_timestamp_token,
                ocsp_present,
                ocsp_responder_fingerprint,
                ocsp_cert_status,
                ocsp_produced_at,
                ocsp_this_update,
                ocsp_next_update,
                ocsp_response_der,
                key_version,
            ) = args
            row = {
                # Espeja `COALESCE($1, gen_random_uuid())` del repositorio real
                # (S1): si el llamador pre-generó el id (xades_local, para que
                # el filename en MinIO lo incluya ANTES del upload) esta fila
                # debe conservar EXACTAMENTE ese id — no uno nuevo — para que
                # el round-trip firma -> verify siga atando la key correcta.
                "id": firma_id_param if firma_id_param is not None else uuid.uuid4(),
                "objeto_tipo": objeto_tipo,
                "objeto_id": objeto_id,
                "formato_solicitado": formato_solicitado,
                "firmante_id": firmante_id,
                "payload_sha256": payload_sha256,
                "firma_blob": firma_blob,
                "provider": provider,
                "sello_tiempo": sello_tiempo,
                "created_at": datetime.now(UTC),
                "certificado_pem": certificado_pem,
                "minio_key_firmado": minio_key_firmado,
                "nivel_conformidad": nivel_conformidad,
                "xades_level": xades_level,
                "tipo_firma": tipo_firma,
                "certificado_fingerprint": certificado_fingerprint,
                "signed_xml_sha256": signed_xml_sha256,
                "tsa_token": tsa_token,
                "tsa_timestamp": tsa_timestamp,
                "tsa_cert_fingerprint": tsa_cert_fingerprint,
                "tsa_acreditado": tsa_acreditado,
                "lt_material_present": lt_material_present,
                "ca_fingerprint": ca_fingerprint,
                "crl_fingerprint": crl_fingerprint,
                "crl_this_update": crl_this_update,
                "crl_next_update": crl_next_update,
                "archive_timestamp_present": archive_timestamp_present,
                "archive_timestamp_at": archive_timestamp_at,
                "archive_tsa_cert_fingerprint": archive_tsa_cert_fingerprint,
                "revocation_provenance": revocation_provenance,
                "archive_timestamp_token": archive_timestamp_token,
                "ocsp_present": ocsp_present,
                "ocsp_responder_fingerprint": ocsp_responder_fingerprint,
                "ocsp_cert_status": ocsp_cert_status,
                "ocsp_produced_at": ocsp_produced_at,
                "ocsp_this_update": ocsp_this_update,
                "ocsp_next_update": ocsp_next_update,
                "ocsp_response_der": ocsp_response_der,
                "key_version": key_version,
            }
            self.rows[str(row["id"])] = row
            return row
        if q.startswith("SELECT") and "FROM firma" in q and "WHERE f.id" in q:
            # La lectura resuelve el NOMBRE del firmante con un LEFT JOIN
            # contra `auth_users` (un comprobante que dice «Firmante» y enseña
            # un UUID no certifica nada). Esta tabla falsa no tiene catálogo de
            # usuarios, así que devuelve `None` — que es exactamente lo que
            # devuelve el JOIN por la izquierda cuando el firmante no es un
            # usuario del catálogo, el caso de los sellos del sistema.
            # `args[1]`, si viene, es el clearance del gate no-read-up de
            # `FirmaRepository.get` (AREA-04-10). Los objetos de esta tabla
            # falsa (índice, acta) no tienen nivel propio: son visibles.
            firma_id = args[0]
            row = self.rows.get(str(firma_id))
            return None if row is None else {**row, "firmante_nombre": None}
        if "FROM auth_users" in q and "is_root" in q:
            # resolve_caller_clearance: usuario normal, clearance PUBLICA.
            return {"is_root": False, "max_level": None}
        if "FROM firma_artefacto_vigente" in q:
            # B10 §8: `GET /firmas/{id}` pregunta por el XML vigente; sin renovaciones es el original.
            row = self.rows.get(str(args[0]))
            return None if row is None else {
                "firma_id": args[0], "minio_key": row["minio_key_firmado"],
                "xml_sha256": row["signed_xml_sha256"], "ordinal": 0,
            }
        if q.startswith("INSERT INTO firma_sello_archivo"):
            # B10 §8: el eslabón 0 de la cadena de sellos de archivo se inserta en la MISMA tx
            # que la firma LTA (sin él, la firma no sería renovable). Esta tabla falsa solo
            # tiene que aceptarlo: lo que contiene lo prueba `tests/integration/`.
            return {"firma_id": args[0], "ordinal": args[1]}
        raise AssertionError(f"Unexpected fetchrow query in _FakeFirmaTable: {query!r}")


@pytest_asyncio.fixture
async def signature_env(client_with_conn, monkeypatch):
    """(client, conn, table, uploads, install_seal, install_tsa_seal) para probar xades_local.

    ``install_seal(ref_or_dict)`` acepta un `SignerRef` único (usado para
    cualquier tenant) o un `dict[str, SignerRef]` para simular sellos
    distintos por tenant (aislamiento, test 11). Un slug sin entrada en el
    dict levanta `SealNotFoundError`, igual que `app.core.keys.load_seal` con
    el filesystem real cuando no hay sello ni fallback `_dev/`.

    ``install_tsa_seal(ref_or_none)`` (Inc.4) instala el par de la TSA local
    parcheando ``app.core.tsa.load_tsa_seal`` (ÚNICA del despliegue, sin
    scoping por tenant — a diferencia del sello institucional).
    ``install_tsa_seal(None)`` simula la AUSENCIA de TSA (levanta
    ``TsaSealNotFoundError``, igual que el filesystem real sin par montado
    ni fallback ``_dev/``). Por defecto (sin llamar a ``install_tsa_seal``),
    la fixture YA instala un par de TSA efímero válido (EKU timeStamping
    crítico) — así que TODO test que solo llame a ``install_seal(...)``
    sigue produciendo una firma XAdES-T completa, reflejando el
    comportamiento real por defecto de producción.
    """
    client, conn = client_with_conn
    # AREA-04-07: el sello institucional en /sign-xml exige X-Internal-Token (lo
    # manda archive-service junto a la identidad firmada). Los tests de este
    # fixture ejercen ESA ruta legítima, así que el cliente lo lleva por defecto.
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "signature_internal_token", "itest-internal-token-abc123")
    client.headers["X-Internal-Token"] = "itest-internal-token-abc123"
    table = _FakeFirmaTable()
    conn.fetchrow.side_effect = table.fetchrow

    uploads: list[dict] = []

    async def _fake_upload(*, tenant_slug, filename, content, uploaded_by=None):
        object_key = f"fake-file-id-{len(uploads) + 1}/{filename}"
        uploads.append(
            {
                "tenant_slug": tenant_slug,
                "filename": filename,
                "content": content,
                "uploaded_by": uploaded_by,
                "object_key": object_key,
            }
        )
        return object_key

    monkeypatch.setattr(storage_client, "upload_signed_xml", _fake_upload)

    def _install_seal(seal):
        if isinstance(seal, SignerRef):
            monkeypatch.setattr("app.services.signing_service.load_seal", lambda slug: seal)
        else:

            def _dispatch(slug: str) -> SignerRef:
                if slug not in seal:
                    raise SealNotFoundError(f"no seal montado para tenant {slug!r}")
                return seal[slug]

            monkeypatch.setattr("app.services.signing_service.load_seal", _dispatch)

    def _install_tsa_seal(seal):
        if seal is None:

            def _raise() -> SignerRef:
                raise TsaSealNotFoundError("no TSA montada (test)")

            monkeypatch.setattr("app.core.tsa.load_tsa_seal", _raise)
        else:
            monkeypatch.setattr("app.core.tsa.load_tsa_seal", lambda: seal)

    _install_tsa_seal(_tsa_signer_ref(*_generate_tsa_cert()))

    return client, conn, table, uploads, _install_seal, _install_tsa_seal


async def _sign_indice(client, *, headers=None, xml: bytes = INDICE_XML):
    return await client.post(
        "/api/v1/signature/sign-xml",
        json={
            "payload_xml": xml.decode(),
            "objeto_tipo": "indice",
            "objeto_id": "exp-1",
            "indice_id": str(uuid.uuid4()),
            "version": 1,
        },
        headers=headers or TENANT_HEADERS,
    )


# ---------------------------------------------------------------------------
# 1) Round-trip XAdES-T (default, Inc.4): sign-xml -> verify=true, sin sobre-declarar (7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_round_trip(signature_env) -> None:
    """Default de producción desde Inc.4: xades_local firma en XAdES-T (con
    sello de tiempo), no XAdES-B "puro" — ver test_xades_t_* más abajo para
    los casos específicos del sello de tiempo (kill-switch, TSA caída,
    alteración post-sellado)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()

    assert body["provider"] == "xades_local"
    assert body["xades_level"] == "XAdES-T"
    assert body["tipo_firma"] == "sello_sistema"
    assert body["nivel_conformidad"] == "xades_b_no_acreditado"
    # RF-FIR-15 — nunca sobre-declarar conformidad (7).
    assert body["acreditado"] is False
    assert body["certificado_fingerprint"] == cert_fingerprint(cert_pem)
    assert len(body["certificado_fingerprint"]) == 64
    assert body["minio_key_firmado"] == uploads[0]["object_key"]
    # firmante_id atribuido por X-User-Id (RF-FIR-11), no por el sello institucional.
    assert body["firmante_id"] == str(USER)
    # (Baja, hallazgo de revisión Inc.4) tsa_acreditado viaja SIEMPRE junto a
    # xades_level para que un consumidor no lea "XAdES-T" como sello
    # acreditado/cualificado — ver app.schemas.firma.FirmaResponse.
    assert body["tsa_acreditado"] is False
    assert body["tsa_timestamp"] is not None

    firma_id = body["firma_id"]
    signed_xml = uploads[0]["content"]
    assert b"<ds:Signature" in signed_xml or b":Signature" in signed_xml
    assert b"SignatureTimeStamp" in signed_xml

    get_resp = await client.get(f"/api/v1/signature/firmas/{firma_id}")
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    assert get_body["xades_level"] == "XAdES-T"
    assert get_body["tsa_acreditado"] is False
    assert get_body["tsa_timestamp"] == body["tsa_timestamp"]

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["provider"] == "xades_local"
    assert vbody["xades_level"] == "XAdES-T"
    assert vbody["acreditado"] is False
    assert vbody["motivo"] is None
    assert vbody["sello_tiempo_valido"] is True
    assert vbody["tsa_timestamp"] is not None


# ---------------------------------------------------------------------------
# 2) Alteración detectada (tamper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_tamper_detected(signature_env) -> None:
    """Con el binding a la firma_id (migración 010), un documento alterado ya
    no coincide con `signed_xml_sha256` — se rechaza por eso, sin necesidad de
    llegar a la verificación criptográfica (ver también
    `test_verify_tamper_detected_by_signature_when_hash_binding_absent` para
    la detección puramente criptográfica en filas anteriores a la migración)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    tampered = signed_xml.replace(b"Contenido original", b"Contenido ALTERADO!!")
    assert tampered != signed_xml

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": tampered.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] == "documento_no_corresponde_a_firma_id"


@pytest.mark.asyncio
async def test_verify_tamper_detected_by_signature_when_hash_binding_absent(signature_env) -> None:
    """Retro-compatibilidad (migración 010): en una fila SIN `signed_xml_sha256`
    (firma anterior a la migración), /verify degrada al comportamiento previo
    — la alteración se detecta por la verificación criptográfica XAdES."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]
    table.rows[firma_id]["signed_xml_sha256"] = None

    tampered = signed_xml.replace(b"Contenido original", b"Contenido ALTERADO!!")
    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": tampered.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] is not None
    assert vbody["motivo"].startswith("firma_invalida")


@pytest.mark.asyncio
async def test_verify_rejects_different_valid_document_same_firma_id(signature_env) -> None:
    """El hallazgo original (Media): el `signed_xml` de OTRO índice, firmado con
    el MISMO sello institucional del tenant, NO debe reportarse `valida=True`
    para un `firma_id` ajeno — aunque sea criptográficamente válido en sí mismo."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp1 = await _sign_indice(client, xml=INDICE_XML)
    firma_id_1 = resp1.json()["firma_id"]

    otro_indice = INDICE_XML.replace(b"Contenido original", b"Otro documento, mismo sello")
    resp2 = await _sign_indice(client, xml=otro_indice)
    assert resp2.status_code == 201
    signed_xml_2 = uploads[1]["content"]

    # signed_xml del SEGUNDO índice, verificado contra el firma_id del PRIMERO.
    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id_1, "payload_xml": signed_xml_2.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] == "documento_no_corresponde_a_firma_id"


# ---------------------------------------------------------------------------
# 2b) Hardening XXE/DoS (RT-16): /verify rechaza XML con DOCTYPE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_rejects_doctype_billion_laughs(signature_env) -> None:
    """`/verify` es público y parsea XML controlado por el llamante: un DOCTYPE
    con entidades anidadas (billion laughs) debe rechazarse ANTES de intentar
    resolverlas, sin lanzar (fail-closed, `valida=False`).

    Sin `signed_xml_sha256` (fila anterior a la migración 010) para que el
    binding a la firma_id (que ya rechazaría este payload por no coincidir el
    hash) no enmascare la comprobación de DOCTYPE que se quiere probar aquí."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    table.rows[firma_id]["signed_xml_sha256"] = None

    malicious = (
        b'<?xml version="1.0"?>'
        b"<!DOCTYPE lolz [\n"
        b'  <!ENTITY lol "lol">\n'
        b'  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b"]>\n"
        b"<indice>&lol2;</indice>"
    )

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": malicious.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] == "dtd_no_permitido"


@pytest.mark.asyncio
async def test_sign_xml_xades_rejects_doctype_payload(signature_env) -> None:
    """El mismo hardening aplica a /sign-xml (mitigado también por PERM_FIRMA,
    pero el parser debe rechazar el DOCTYPE en vez de intentar resolverlo)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    malicious = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE indice [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<indice>&xxe;</indice>"
    )

    resp = await _sign_indice(client, xml=malicious)
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "payload_xml_invalido"


# ---------------------------------------------------------------------------
# 3) Huella de certificado no coincide con el sello registrado al firmar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_fingerprint_mismatch(signature_env) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    # Simula que la fila persistida quedó con una huella distinta a la del
    # certificado realmente embebido en la firma (p. ej. corrupción o rotación
    # de sello mal registrada) — verify_xades debe fallar por huella, no por
    # criptografía (la firma en sí sigue siendo válida).
    table.rows[firma_id]["certificado_fingerprint"] = "0" * 64

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] == "fingerprint_no_coincide_sello_tenant"


# ---------------------------------------------------------------------------
# 4) Certificado fuera de vigencia (expirado)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_expired_cert_detected(signature_env) -> None:
    """ARC-M2: un sello caducado NO firma (503 `sello_institucional_vencido`).

    Antes se firmaba, se archivaba (WORM) y solo `/verify` lo rechazaba después: el sistema
    emitía firmas que nacían inverificables. La vigencia se exige ANTES de producir nada.
    """
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    now = datetime.now(UTC)
    cert_pem, key_pem = _generate_cert(
        "orpycamcp-dev-expired",
        not_before=now - timedelta(days=400),
        not_after=now - timedelta(days=30),
    )
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "sello_institucional_vencido"
    assert uploads == [], "no se sube nada a MinIO"
    assert not table.rows, "no se persiste ninguna firma"


# ---------------------------------------------------------------------------
# 5) Atribución: firmante_id == X-User-Id + audit 'firma.creada'; sin header -> 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_attribution_and_audit(signature_env) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["firmante_id"] == str(USER)

    audit_calls = [
        c for c in conn.execute.call_args_list if "INSERT INTO public.audit_log" in str(c.args[0])
    ]
    assert len(audit_calls) == 1
    call_args = audit_calls[0].args
    # _INSERT_SQL, tenant, service, actor, canal, action, object_type, object_ref, payload_json, prev_hash, hash
    assert call_args[3] == str(USER)  # actor
    assert call_args[5] == "firma.creada"  # action
    payload = json.loads(call_args[8])
    assert payload["tipo_firma"] == "sello_sistema"
    assert payload["provider"] == "xades_local"
    assert payload["xades_level"] == "XAdES-T"
    assert payload["nivel_conformidad"] == "xades_b_no_acreditado"
    assert payload["certificado_fingerprint"] == cert_fingerprint(cert_pem)


@pytest.mark.asyncio
async def test_sign_xml_xades_requires_auth() -> None:
    """Sin X-User-Id, /sign-xml con objeto_tipo=indice también devuelve 401
    (el gate de autenticación corre antes de seleccionar el proveedor)."""
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_tenant_conn
    from app.main import app
    from tests.conftest import _make_mock_conn

    conn = _make_mock_conn()

    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<indice/>", "objeto_tipo": "indice"},
                headers={"X-Tenant-Slug": "demo"},  # sin X-User-Id
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 6) PERM_FIRMA obligatorio también para el camino xades_local
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_requires_perm_firma() -> None:
    """PERM_FIRMA denegado -> 403, incluso para objeto_tipo=indice.

    No requiere sello/upload mockeados: require_permission corre ANTES de que
    el router seleccione el proveedor.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_tenant_conn
    from app.main import app
    from tests.conftest import _make_mock_conn

    conn = _make_mock_conn()
    conn.fetchval.return_value = None  # check_permission -> denegado

    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/sign-xml",
                json={"payload_xml": "<indice/>", "objeto_tipo": "indice"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "forbidden"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 11) Aislamiento del sello por tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_seal_isolated_per_tenant(signature_env) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_a, key_a = _generate_cert("tenant-a")
    cert_b, key_b = _generate_cert("tenant-b")
    install_seal(
        {
            "tenant-a": _signer_ref(cert_a, key_a),
            "tenant-b": _signer_ref(cert_b, key_b),
        }
    )

    resp_a = await _sign_indice(client, headers={"X-Tenant-Slug": "tenant-a", "X-User-Id": str(USER)})
    resp_b = await _sign_indice(client, headers={"X-Tenant-Slug": "tenant-b", "X-User-Id": str(USER)})

    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    fp_a = resp_a.json()["certificado_fingerprint"]
    fp_b = resp_b.json()["certificado_fingerprint"]

    assert fp_a == cert_fingerprint(cert_a)
    assert fp_b == cert_fingerprint(cert_b)
    assert fp_a != fp_b  # tenant A nunca resuelve el sello de tenant B


@pytest.mark.asyncio
async def test_sign_xml_xades_seal_unavailable_returns_503(signature_env) -> None:
    """Ningún sello montado (ni de tenant ni _dev) -> 503 signing_seal_unavailable,
    tratado como fallo de infraestructura best-effort por el llamador (RT-16)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    install_seal({})  # ningún tenant tiene sello

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "signing_seal_unavailable"


@pytest.mark.asyncio
async def test_sign_xml_xades_storage_upload_failure_returns_503(signature_env, monkeypatch) -> None:
    """storage-service no acepta la subida -> 503 signed_index_upload_failed,
    la fila `firma` no llega a persistirse (fetchrow del INSERT nunca se llama)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    async def _failing_upload(*, tenant_slug, filename, content, uploaded_by=None):
        raise storage_client.StorageUploadError("storage-service inalcanzable: boom")

    monkeypatch.setattr(storage_client, "upload_signed_xml", _failing_upload)

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "signed_index_upload_failed"
    assert table.rows == {}


# ---------------------------------------------------------------------------
# XAdES-T (E06, Inc.4, RT-16) — sello de tiempo RFC 3161 sobre el índice
# ---------------------------------------------------------------------------
#
# El round-trip básico (sign produce SignatureTimeStamp, verify lo valida,
# xades_level=="XAdES-T", acreditado=False) ya está cubierto por
# `test_sign_xml_xades_round_trip` de la sección 1 — la fixture instala una
# TSA por defecto, así que ESE test YA ejercita el caso (a) del contrato del
# incremento. Aquí van los casos específicos del sello de tiempo: binding al
# SignatureValue, kill-switch, TSA caída y certificado TSA mal formado.


@pytest.mark.asyncio
async def test_xades_t_signature_timestamp_structure(signature_env) -> None:
    """(a) El XML firmado lleva xades:UnsignedProperties/.../SignatureTimeStamp
    con un EncapsulatedTimeStamp base64 decodificable, como HERMANO (no
    reemplazo) de xades:SignedProperties — la firma XAdES-B de base sigue
    intacta (ver también el round-trip de la sección 1, que ya prueba que
    /verify valida ambas capas)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    signed_xml = uploads[0]["content"]

    root = etree.fromstring(signed_xml)
    ns = {
        "ds": "http://www.w3.org/2000/09/xmldsig#",
        "xades": "http://uri.etsi.org/01903/v1.3.2#",
    }
    qp = root.find(".//xades:QualifyingProperties", namespaces=ns)
    assert qp is not None
    assert qp.find("xades:SignedProperties", namespaces=ns) is not None
    ts_el = qp.find(
        "xades:UnsignedProperties/xades:UnsignedSignatureProperties/xades:SignatureTimeStamp",
        namespaces=ns,
    )
    assert ts_el is not None
    encapsulated = ts_el.find("xades:EncapsulatedTimeStamp", namespaces=ns)
    assert encapsulated is not None and encapsulated.text
    token_der = b64decode(encapsulated.text.strip())
    assert len(token_der) > 0
    c14n_method = ts_el.find("ds:CanonicalizationMethod", namespaces=ns)
    assert c14n_method.get("Algorithm") == "http://www.w3.org/2001/10/xml-exc-c14n#"


@pytest.mark.asyncio
async def test_xades_t_token_binds_to_signature_value(signature_env) -> None:
    """(b) El messageImprint del TimeStampToken cubre `ds:SignatureValue`:
    alterar ese valor DESPUÉS de sellado invalida el sello. Verificado a
    nivel de `app.core.tsa` directamente (no vía /verify HTTP) porque
    alterar `ds:SignatureValue` en el XML COMPLETO también rompe la firma
    XAdES-B de base — /verify nunca llega a evaluar el sello de tiempo en
    ese caso (ver `test_xades_t_altered_signature_value_fails_base_xades`
    para ese escenario end-to-end)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    tsa_cert, tsa_key = _generate_tsa_cert()
    install_tsa_seal(_tsa_signer_ref(tsa_cert, tsa_key))

    original_el = etree.fromstring(
        b'<ds:SignatureValue xmlns:ds="http://www.w3.org/2000/09/xmldsig#">QUJD</ds:SignatureValue>'
    )
    original_c14n = etree.tostring(original_el, method="c14n", exclusive=True)

    expected_fp = cert_fingerprint(tsa_cert)
    token = tsa_module.build_timestamp_token(original_c14n)
    outcome_ok = tsa_module.verify_timestamp_token(
        token.token_der, original_c14n, expected_tsa_fingerprint=expected_fp
    )
    assert outcome_ok.valido is True
    assert outcome_ok.gen_time is not None

    tampered_el = etree.fromstring(
        b'<ds:SignatureValue xmlns:ds="http://www.w3.org/2000/09/xmldsig#">WFla</ds:SignatureValue>'
    )
    tampered_c14n = etree.tostring(tampered_el, method="c14n", exclusive=True)
    outcome_bad = tsa_module.verify_timestamp_token(
        token.token_der, tampered_c14n, expected_tsa_fingerprint=expected_fp
    )
    assert outcome_bad.valido is False
    assert outcome_bad.motivo == "message_imprint_no_coincide"


@pytest.mark.asyncio
async def test_xades_t_altered_signature_value_fails_base_xades(signature_env) -> None:
    """Escenario end-to-end complementario a (b): alterar `ds:SignatureValue`
    en el XML COMPLETO tras sellado invalida la firma XAdES-B de base — la
    respuesta es `valida=False` por la criptografía XMLDSig, con
    `sello_tiempo_valido=None` (nunca se llega a evaluar, ver
    `app.providers.local.verify_xades`)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    root = etree.fromstring(signed_xml)
    sig_value_el = root.find(
        ".//{http://www.w3.org/2000/09/xmldsig#}Signature"
        "/{http://www.w3.org/2000/09/xmldsig#}SignatureValue"
    )
    sig_value_el.text = "QUJDdGFtcGVyZWQ="
    tampered_xml = etree.tostring(root)

    table.rows[firma_id]["signed_xml_sha256"] = None  # aísla la comprobación criptográfica
    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": tampered_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["sello_tiempo_valido"] is None


@pytest.mark.asyncio
async def test_xades_t_kill_switch_produces_xades_b(signature_env, monkeypatch) -> None:
    """(c) `signer_timestamp_enabled=False` -> XAdES-B sin SignatureTimeStamp,
    exactamente el comportamiento previo al Incremento 4."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))
    monkeypatch.setattr(settings, "signer_timestamp_enabled", False)

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["xades_level"] == "XAdES-B"
    signed_xml = uploads[0]["content"]
    assert b"SignatureTimeStamp" not in signed_xml

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": body["firma_id"], "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["xades_level"] == "XAdES-B"
    # Sin SignatureTimeStamp — no aplica, NO es un fallo (None, no False).
    assert vbody["sello_tiempo_valido"] is None


@pytest.mark.asyncio
async def test_xades_t_tsa_unavailable_returns_503(signature_env) -> None:
    """(d) TSA no disponible + kill-switch en True (default) -> /sign-xml
    falla con 503, fail-closed (mismo trato que la ausencia de sello
    institucional en Inc.1) — nunca degrada en silencio a XAdES-B."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))
    install_tsa_seal(None)

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "timestamp_authority_unavailable"
    assert table.rows == {}


@pytest.mark.asyncio
async def test_xades_t_remote_tsa_url_configured_fails_explicit(signature_env, monkeypatch) -> None:
    """`settings.tsa_url` puesto -> intenta el cliente remoto (stub, Inc.4) y
    falla explícito — NUNCA cae en silencio a la TSA local aunque esté
    montada y disponible (ver app.core.tsa._request_remote_timestamp)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))
    monkeypatch.setattr(settings, "tsa_url", "https://tsa.example.test/timestamp")

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "timestamp_authority_unavailable"


@pytest.mark.asyncio
async def test_xades_t_tsa_cert_without_critical_eku_rejected(signature_env) -> None:
    """RFC 3161 §2.3: un certificado de TSA sin EKU timeStamping CRÍTICO se
    rechaza al sellar (fail-closed) — no se emite un token con un cert mal
    emitido."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    bad_tsa_cert, bad_tsa_key = _generate_tsa_cert(eku_critical=False)
    install_tsa_seal(_tsa_signer_ref(bad_tsa_cert, bad_tsa_key))

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "timestamp_authority_unavailable"


@pytest.mark.asyncio
async def test_xades_t_verify_detects_tsa_cert_without_eku() -> None:
    """`_assert_timestamping_eku` (el mismo guardado que usan tanto
    `_build_local_timestamp_token` al sellar como `verify_timestamp_token`
    al verificar, RFC 3161 §2.3) rechaza un certificado sin EKU timeStamping
    crítico y acepta uno correctamente emitido."""
    good_cert_pem, _ = _generate_tsa_cert()
    good_cert = x509.load_pem_x509_certificate(good_cert_pem)
    tsa_module._assert_timestamping_eku(good_cert)  # no debe lanzar

    bad_cert_pem, _ = _generate_tsa_cert(with_eku=False)
    bad_cert = x509.load_pem_x509_certificate(bad_cert_pem)
    with pytest.raises(tsa_module.TsaUnavailableError, match="eku_timestamping"):
        tsa_module._assert_timestamping_eku(bad_cert)


# ---------------------------------------------------------------------------
# 8) Pinning del certificado de la TSA contra firma.tsa_cert_fingerprint
#    (Media, hallazgo de revisión post-Inc.4 — SignatureTimeStamp es una
#    propiedad NO FIRMADA, así que sin este pinning cualquier cert
#    autofirmado con EKU timeStamping produce un sello que /verify reporta
#    válido con un genTime arbitrario).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xades_t_verify_timestamp_token_pins_tsa_fingerprint(signature_env) -> None:
    """`verify_timestamp_token` sin `expected_tsa_fingerprint` (fila legacy
    sin la columna poblada) o con uno que no coincide con el certificado
    embebido en el token debe fallar-cerrado — nunca `valido=True` por
    omitir o descartar la comprobación."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    tsa_cert, tsa_key = _generate_tsa_cert()
    install_tsa_seal(_tsa_signer_ref(tsa_cert, tsa_key))

    sig_value_el = etree.fromstring(
        b'<ds:SignatureValue xmlns:ds="http://www.w3.org/2000/09/xmldsig#">QUJD</ds:SignatureValue>'
    )
    sig_value_c14n = etree.tostring(sig_value_el, method="c14n", exclusive=True)
    token = tsa_module.build_timestamp_token(sig_value_c14n)
    correct_fp = cert_fingerprint(tsa_cert)

    outcome_ok = tsa_module.verify_timestamp_token(
        token.token_der, sig_value_c14n, expected_tsa_fingerprint=correct_fp
    )
    assert outcome_ok.valido is True

    outcome_missing = tsa_module.verify_timestamp_token(
        token.token_der, sig_value_c14n, expected_tsa_fingerprint=None
    )
    assert outcome_missing.valido is False
    assert outcome_missing.motivo == "tsa_fingerprint_no_registrado"

    foreign_cert, _ = _generate_tsa_cert(cn="otra-tsa")
    outcome_mismatch = tsa_module.verify_timestamp_token(
        token.token_der, sig_value_c14n, expected_tsa_fingerprint=cert_fingerprint(foreign_cert)
    )
    assert outcome_mismatch.valido is False
    assert outcome_mismatch.motivo == "tsa_cert_fingerprint_no_coincide"


@pytest.mark.asyncio
async def test_xades_t_verify_rejects_forged_timestamp_from_foreign_tsa(
    signature_env, monkeypatch
) -> None:
    """Escenario end-to-end del hallazgo: un atacante con un XML XAdES-B
    GENUINO de este tenant (fila legacy, sin sello de tiempo — `tsa_token`/
    `tsa_cert_fingerprint` en NULL) le anexa su PROPIO `xades:
    SignatureTimeStamp`, sellado con SU PROPIA TSA (par autofirmado, EKU
    timeStamping crítico) sobre el `ds:SignatureValue` REAL. Como esa
    propiedad no está firmada, la firma XAdES-B de base sigue validando —
    pero `/verify` debe reportar `sello_tiempo_valido=False`, nunca `True`
    con el `genTime` que eligió el atacante."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))
    # Firma XAdES-B "pura" (sin TSA legítima) — reproduce la fila legacy del
    # hallazgo, donde tsa_cert_fingerprint nunca se llegó a poblar.
    monkeypatch.setattr(settings, "signer_timestamp_enabled", False)

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]
    assert b"SignatureTimeStamp" not in signed_xml
    assert table.rows[firma_id]["tsa_cert_fingerprint"] is None

    # El atacante monta su propia TSA y sella el ds:SignatureValue real.
    attacker_cert, attacker_key = _generate_tsa_cert(cn="atacante-tsa")
    monkeypatch.setattr(
        tsa_module, "load_tsa_seal", lambda: _tsa_signer_ref(attacker_cert, attacker_key)
    )

    root = etree.fromstring(signed_xml)
    ds_ns = {"ds": "http://www.w3.org/2000/09/xmldsig#"}
    sig_value_el = root.find(".//ds:Signature/ds:SignatureValue", namespaces=ds_ns)
    assert sig_value_el is not None
    sig_value_c14n = etree.tostring(sig_value_el, method="c14n", exclusive=True)
    forged_token = tsa_module.build_timestamp_token(sig_value_c14n)

    from app.providers.local import _insert_signature_timestamp

    _insert_signature_timestamp(root, forged_token)
    forged_xml = etree.tostring(root, xml_declaration=False, encoding="UTF-8")
    assert b"SignatureTimeStamp" in forged_xml

    # También legacy en `signed_xml_sha256`: si no, el hash del documento
    # completo ya no coincidiría tras la inyección y /verify fallaría por esa
    # comprobación PREVIA, sin llegar siquiera a evaluar el sello forjado.
    table.rows[firma_id]["signed_xml_sha256"] = None

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": forged_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    # La firma XAdES-B de base sigue siendo válida (SignatureTimeStamp es una
    # propiedad NO FIRMADA) — lo único que debe fallar es el sello forjado.
    assert vbody["valida"] is True
    assert vbody["sello_tiempo_valido"] is False
    assert vbody["tsa_timestamp"] is None


# ---------------------------------------------------------------------------
# XAdES-LT/LTA — material de validación a largo plazo (E06, RT-16)
# ---------------------------------------------------------------------------
#
# `settings.signer_lt_enabled` es `False` por defecto (kill-switch) — cada
# test que ejercita el camino LT/LTA lo activa explícitamente vía monkeypatch
# y lo restaura NO hace falta (monkeypatch revierte solo al terminar el test).
# El material CA/CRL se genera enteramente en memoria (`_generate_ca` /
# `_issue_seal_signed_by_ca` / `_build_crl`) — nunca toca el filesystem ni
# `scripts/gen-dev-ca.sh`.


@pytest.mark.asyncio
async def test_xades_lt_upgrades_to_lta_with_ca_material(signature_env, monkeypatch) -> None:
    """(a) Con `signer_lt_enabled=True` y material CA/CRL montado, la firma
    sube DIRECTAMENTE a XAdES-LTA (LT y LTA se producen SIEMPRE juntos al
    firmar, RT-16 Pieza 5): CertificateValues=SOLO la CA (no duplica el sello,
    ya en KeyInfo), RevocationValues=la CRL, ArchiveTimeStamp presente y en
    ORDEN ETSI (después de SignatureTimeStamp/CertificateValues/RevocationValues)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()

    assert body["xades_level"] == "XAdES-LTA"
    assert body["nivel_conformidad"] == "xades_lta_no_acreditado"
    # RF-FIR-15 — nunca sobre-declarar conformidad, ni siquiera en LTA.
    assert body["acreditado"] is False
    assert body["lt_material_present"] is True
    assert body["archive_timestamp_present"] is True
    assert body["revocation_provenance"] == "dev"
    assert body["crl_next_update"] is not None

    signed_xml = uploads[0]["content"]
    root = etree.fromstring(signed_xml)
    ns = {
        "ds": "http://www.w3.org/2000/09/xmldsig#",
        "xades": "http://uri.etsi.org/01903/v1.3.2#",
        "xadesv141": "http://uri.etsi.org/01903/v1.4.1#",
    }
    unsigned_sig_props = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties", namespaces=ns
    )
    assert unsigned_sig_props is not None
    children = list(unsigned_sig_props)
    tags = [etree.QName(c).localname for c in children]
    # Orden ETSI EN 319 132: SignatureTimeStamp, CertificateValues,
    # RevocationValues, ArchiveTimeStamp — SIEMPRE el último.
    assert tags == [
        "SignatureTimeStamp",
        "CertificateValues",
        "RevocationValues",
        "ArchiveTimeStamp",
    ]

    cert_values = unsigned_sig_props.find("xades:CertificateValues", namespaces=ns)
    encapsulated_certs = cert_values.findall("xades:EncapsulatedX509Certificate", namespaces=ns)
    # SOLO la CA — el sello ya viaja en ds:KeyInfo, no se duplica (EN 319 132 §5.2.9).
    assert len(encapsulated_certs) == 1
    embedded_ca_der = b64decode(encapsulated_certs[0].text.strip())
    assert embedded_ca_der == ca_cert.public_bytes(serialization.Encoding.DER)

    rev_values = unsigned_sig_props.find(
        "xades:RevocationValues/xades:CRLValues/xades:EncapsulatedCRLValue", namespaces=ns
    )
    assert b64decode(rev_values.text.strip()) == crl_der

    archive_ts = unsigned_sig_props.find("xadesv141:ArchiveTimeStamp", namespaces=ns)
    assert archive_ts is not None
    # El CONTENEDOR es v1.4.1, pero sus hijos siguen en v1.3.2 (mismo
    # XAdESTimeStampType que SignatureTimeStamp — ver app.providers.local).
    assert archive_ts.find("xades:EncapsulatedTimeStamp", namespaces=ns) is not None
    assert archive_ts.find("xadesv141:EncapsulatedTimeStamp", namespaces=ns) is None


@pytest.mark.asyncio
async def test_xades_lt_verify_validates_chain_crl_and_archive_timestamp(
    signature_env, monkeypatch
) -> None:
    """(b) /verify de una firma XAdES-LTA reporta valida=True, xades_level
    recalculado a "XAdES-LTA" (nivel REALMENTE verificado, no el persistido en
    la fila) y sello_tiempo_valido=True — round-trip completo end-to-end."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["xades_level"] == "XAdES-LTA"
    assert vbody["acreditado"] is False
    assert vbody["motivo"] is None
    assert vbody["sello_tiempo_valido"] is True
    assert vbody["lt_material_present"] is True
    assert vbody["archive_timestamp_present"] is True
    assert vbody["revocation_provenance"] == "dev"
    assert vbody["crl_next_update"] is not None


@pytest.mark.asyncio
async def test_xades_lt_revoked_seal_fails_closed(signature_env, monkeypatch) -> None:
    """(c) Un sello cuyo serial SÍ aparece en la CRL de la CA -> verify
    fail-closed (`certificado_sello_revocado`), aunque la firma XAdES-B/-T de
    base sea criptográficamente válida."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    # CRL que YA revoca el serial de este sello concreto (simula un sello
    # comprometido/rotado tras el cual la CA local lo lista en su próxima CRL).
    crl_der = _build_crl(ca_key, ca_cert, revoked_serials=(seal_cert.serial_number,))
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] == "certificado_sello_revocado"


@pytest.mark.asyncio
async def test_xades_lt_ca_binding_rejects_misconfigured_foreign_ca(
    signature_env, monkeypatch
) -> None:
    """(d) Aislamiento CA<->tenant (RT-16, Pieza 2/6.1): un sello NUNCA emitido
    por la CA que se le asocia (p. ej. un operador copia por error el
    ca.crt/crl.der de OTRO tenant junto al sello de este) debe fallar la
    verificación de la cadena seal->CA (`cadena_seal_ca_invalida`) — nunca
    reportarse válido solo porque el material "parece" una CA correcta."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    # CA legítima de un tenant distinto ("tenant B") — bien formada, pero
    # NUNCA emitió este sello.
    foreign_ca_key, foreign_ca_cert = _generate_ca(cn="ca-tenant-b")
    # Sello AUTOFIRMADO real de "tenant A" (como en Inc.1, sin CA) —
    # misconfigurado con el ca.crt/crl.der ajeno.
    seal_cert_pem, seal_key_pem = _generate_cert("orpycamcp-dev-tenant-a")
    crl_der = _build_crl(foreign_ca_key, foreign_ca_cert)
    install_seal(
        _lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(foreign_ca_cert), crl_der)
    )

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] is not None
    assert vbody["motivo"].startswith("cadena_seal_ca_invalida")


@pytest.mark.asyncio
async def test_xades_lt_downgrade_no_ca_material_stays_at_t_and_audits(
    signature_env, monkeypatch
) -> None:
    """(e) `signer_lt_enabled=True` pero el tenant NO tiene ca.crt/crl.der
    montado (el caso normal fuera de `scripts/gen-dev-ca.sh`) -> degradación
    OBSERVABLE: la firma se queda en XAdES-T (nunca falla), y queda una
    entrada de auditoría DEDICADA `firma.lt_downgrade_no_ca` — nunca en
    silencio."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))  # sin ca_cert_pem/crl_der

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["xades_level"] == "XAdES-T"
    assert body["lt_material_present"] is False
    assert body["archive_timestamp_present"] is False

    downgrade_calls = [
        c
        for c in conn.execute.call_args_list
        if "INSERT INTO public.audit_log" in str(c.args[0])
    ]
    assert len(downgrade_calls) == 2  # firma.creada + firma.lt_downgrade_no_ca
    actions = [c.args[5] for c in downgrade_calls]
    assert "firma.lt_downgrade_no_ca" in actions
    downgrade_payload = json.loads(
        next(c for c in downgrade_calls if c.args[5] == "firma.lt_downgrade_no_ca").args[8]
    )
    assert downgrade_payload["xades_level"] == "XAdES-T"
    assert downgrade_payload["signer_lt_enabled"] is True
    assert downgrade_payload["motivo"] == "sin_material_ca_local"


@pytest.mark.asyncio
async def test_xades_lt_disabled_kill_switch_never_audits_downgrade(
    signature_env, monkeypatch
) -> None:
    """`signer_lt_enabled=False` (default) es simplemente el feature apagado —
    NUNCA es una "degradación": no debe emitirse `firma.lt_downgrade_no_ca`."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    # signer_lt_enabled ya es False por defecto — no hace falta monkeypatch.
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    assert resp.json()["xades_level"] == "XAdES-T"

    audit_calls = [
        c for c in conn.execute.call_args_list if "INSERT INTO public.audit_log" in str(c.args[0])
    ]
    assert len(audit_calls) == 1  # solo firma.creada
    assert audit_calls[0].args[5] == "firma.creada"


@pytest.mark.asyncio
async def test_xades_lt_require_without_ca_material_returns_503(
    signature_env, monkeypatch
) -> None:
    """(f) `signer_lt_require=True` + sin material CA -> 503
    `lt_material_required`, fail-closed (para despliegues que exigen
    conformidad LT/LTA estricta) — la fila `firma` NUNCA llega a persistirse."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_lt_require", True)

    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))  # sin ca_cert_pem/crl_der

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "lt_material_required"
    assert table.rows == {}


@pytest.mark.asyncio
async def test_xades_lt_verify_pins_ca_fingerprint(monkeypatch) -> None:
    """(g) Pinning (RT-16, Pieza 6.a) — mismo patrón que
    `test_xades_t_verify_timestamp_token_pins_tsa_fingerprint`: sin
    `expected_ca_fingerprint` (fila legacy o T-only) o con uno que no coincide
    con la CA REALMENTE embebida, `verify_xades` falla-cerrado. Llamado
    directo a la función (no vía HTTP, sin pasar por ``signature_env``/router)
    para aislar el pinning de cualquier otra comprobación previa."""
    from app.providers.base import PreparedDoc
    from app.providers.local import LocalSigner, verify_xades

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_timestamp_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)

    tsa_cert_pem, tsa_key_pem = _generate_tsa_cert()
    monkeypatch.setattr(
        tsa_module, "load_tsa_seal", lambda: _tsa_signer_ref(tsa_cert_pem, tsa_key_pem)
    )

    prepared = PreparedDoc(xml_bytes=INDICE_XML, objeto_tipo="indice", objeto_id="exp-1")
    signer_ref = _lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der)
    signed = await LocalSigner().sign(prepared, signer_ref)

    fp = cert_fingerprint(seal_cert_pem)
    correct_ca_fp = signed.extra["ca_fingerprint"]

    outcome_ok = verify_xades(
        signed.signed_xml,
        fp,
        expected_tsa_fingerprint=signed.extra["tsa_cert_fingerprint"],
        expected_ca_fingerprint=correct_ca_fp,
    )
    assert outcome_ok.valida is True

    outcome_missing = verify_xades(
        signed.signed_xml,
        fp,
        expected_tsa_fingerprint=signed.extra["tsa_cert_fingerprint"],
        expected_ca_fingerprint=None,
    )
    assert outcome_missing.valida is False
    assert outcome_missing.motivo == "ca_fingerprint_no_registrado"

    foreign_ca_key, foreign_ca_cert = _generate_ca(cn="otra-ca")
    outcome_mismatch = verify_xades(
        signed.signed_xml,
        fp,
        expected_tsa_fingerprint=signed.extra["tsa_cert_fingerprint"],
        expected_ca_fingerprint=cert_fingerprint(_ca_cert_pem(foreign_ca_cert)),
    )
    assert outcome_mismatch.valida is False
    assert outcome_mismatch.motivo == "ca_cert_fingerprint_no_coincide"


@pytest.mark.asyncio
async def test_xades_lt_retrocompat_t_and_b_signatures_still_verify(
    signature_env, monkeypatch
) -> None:
    """(h) Retrocompatibilidad: firmas XAdES-T y XAdES-B "puras" (sin
    CertificateValues) verifican EXACTAMENTE igual que antes de este
    incremento, incluso con `settings.signer_lt_enabled=True` a nivel de
    servicio (el nivel LT/LTA solo se evalúa si la firma CONCRETA trae
    CertificateValues)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    # T "puro": sello SIN material CA -> se queda en T (ver test de downgrade).
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["xades_level"] == "XAdES-T"
    assert vbody["lt_material_present"] is False
    assert vbody["archive_timestamp_present"] is False


@pytest.mark.asyncio
async def test_xades_lt_acreditado_always_false(signature_env, monkeypatch) -> None:
    """(i) RF-FIR-15: `acreditado` es SIEMPRE False incluso cuando la firma
    escala a XAdES-LTA y verifica correctamente — la CA es de desarrollo, sin
    acreditación ONAC."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    body = resp.json()
    assert body["xades_level"] == "XAdES-LTA"
    assert body["acreditado"] is False

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": body["firma_id"], "payload_xml": uploads[0]["content"].decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.json()["acreditado"] is False


# ---------------------------------------------------------------------------
# (j) Determinismo explícito del ArchiveTimeStamp — build y verify DEBEN usar
#     el MISMO helper de canonicalización (_archive_ts_covered_bytes) sobre
#     CUALQUIER re-parseo del mismo XML firmado, o el sello nunca validaría
#     fuera del proceso que lo produjo (RT-16, requisito no negociable).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xades_lta_archive_timestamp_covered_bytes_deterministic_across_reparse(
    monkeypatch,
) -> None:
    """Llamada directa a `_archive_ts_covered_bytes` (sin pasar por HTTP): el
    MISMO `signed_xml` re-parseado dos veces de forma INDEPENDIENTE (simula
    que build ocurre en un proceso y verify en una request HTTP distinta,
    posiblemente en otra réplica) produce EXACTAMENTE los mismos bytes
    cubiertos. Si hubiera cualquier no-determinismo en la canonicalización
    (prefijos de namespace, orden de atributos, espacios), esta igualdad
    fallaría y el `ArchiveTimeStamp` jamás validaría fuera del árbol lxml
    original en memoria — round-trip verde es el contrato mínimo."""
    from app.providers.base import PreparedDoc
    from app.providers.local import LocalSigner, _archive_ts_covered_bytes

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    # Este test no pasa por `signature_env` (no necesita cliente HTTP/tabla
    # fake) — instala la TSA directamente, mismo patrón que
    # `test_xades_lt_verify_pins_ca_fingerprint`.
    tsa_cert_pem, tsa_key_pem = _generate_tsa_cert()
    monkeypatch.setattr(
        tsa_module, "load_tsa_seal", lambda: _tsa_signer_ref(tsa_cert_pem, tsa_key_pem)
    )
    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    signer_ref = _lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der)

    prepared = PreparedDoc(xml_bytes=INDICE_XML, objeto_tipo="indice", objeto_id="exp-1")
    signed = await LocalSigner().sign(prepared, signer_ref)
    assert signed.xades_level == "XAdES-LTA"

    root_a = etree.fromstring(signed.signed_xml)
    root_b = etree.fromstring(signed.signed_xml)
    covered_a = _archive_ts_covered_bytes(root_a)
    covered_b = _archive_ts_covered_bytes(root_b)
    assert covered_a == covered_b
    assert len(covered_a) > 0

    # El propio ArchiveTimeStamp embebido debe seguir validando contra este
    # recálculo independiente — cierra el lazo determinismo -> verificación real.
    ns = {
        "xades": "http://uri.etsi.org/01903/v1.3.2#",
        "xadesv141": "http://uri.etsi.org/01903/v1.4.1#",
    }
    archive_ts = root_b.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties/xadesv141:ArchiveTimeStamp",
        namespaces=ns,
    )
    encapsulated = archive_ts.find("xades:EncapsulatedTimeStamp", namespaces=ns)
    token_der = b64decode(encapsulated.text.strip())
    outcome = tsa_module.verify_timestamp_token(
        token_der, covered_a, expected_tsa_fingerprint=signed.extra["archive_tsa_cert_fingerprint"]
    )
    assert outcome.valido is True


# ---------------------------------------------------------------------------
# (k) Distinción del nivel REALMENTE verificado: LT (sin ArchiveTimeStamp)
#     vs. LTA (con uno válido) vs. fail-closed (con uno presente pero
#     corrupto) — ver `app.providers.local.verify_xades`, sección "XAdES-LT/LTA".
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xades_lt_verify_reports_lt_level_when_archive_timestamp_absent(
    signature_env, monkeypatch
) -> None:
    """El `xadesv141:ArchiveTimeStamp` es una propiedad NO FIRMADA: removerlo
    tras firmar (simulando un XML LTA truncado en tránsito, o un firma_id de
    una fila anterior a que este servicio empezara a producir el sello de
    archivo) NO invalida la firma XAdES-B/-T de base. `/verify` debe reportar
    el nivel REALMENTE verificado para ESTE documento: "XAdES-LT" (no "XAdES-
    LTA", que exige el ArchiveTimeStamp presente Y válido), con `valida=True`
    y `archive_timestamp_present=False`."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    ns = {
        "xades": "http://uri.etsi.org/01903/v1.3.2#",
        "xadesv141": "http://uri.etsi.org/01903/v1.4.1#",
    }
    root = etree.fromstring(signed_xml)
    unsigned_sig_props = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties", namespaces=ns
    )
    archive_ts = unsigned_sig_props.find("xadesv141:ArchiveTimeStamp", namespaces=ns)
    assert archive_ts is not None
    unsigned_sig_props.remove(archive_ts)
    stripped_xml = etree.tostring(root, xml_declaration=False, encoding="UTF-8")
    assert stripped_xml != signed_xml

    # Aísla del binding a firma_id (migración 010) — lo que se quiere probar
    # aquí es la distinción de nivel, no el hash-binding ya cubierto arriba.
    table.rows[firma_id]["signed_xml_sha256"] = None
    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": stripped_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["xades_level"] == "XAdES-LT"
    assert vbody["archive_timestamp_present"] is False
    assert vbody["lt_material_present"] is True
    assert vbody["motivo"] is None


@pytest.mark.asyncio
async def test_xades_lt_verify_fails_closed_when_archive_timestamp_tampered(
    signature_env, monkeypatch
) -> None:
    """A diferencia de la AUSENCIA total (degradación observable a "XAdES-LT"
    del test anterior), un `ArchiveTimeStamp` que SIGUE PRESENTE pero cuyo
    token no valida (corrupto/alterado) SÍ invalida la firma completa
    (fail-closed) — material LT presente-pero-inválido nunca se tolera, a
    diferencia del sello de tiempo XAdES-T "puro" ausente (ver docstring de
    `verify_xades`)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    ns = {
        "xades": "http://uri.etsi.org/01903/v1.3.2#",
        "xadesv141": "http://uri.etsi.org/01903/v1.4.1#",
    }
    root = etree.fromstring(signed_xml)
    archive_ts = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties/xadesv141:ArchiveTimeStamp",
        namespaces=ns,
    )
    encapsulated = archive_ts.find("xades:EncapsulatedTimeStamp", namespaces=ns)
    assert encapsulated is not None and encapsulated.text
    # Base64 válido, contenido CMS/DER inválido — corrompe SOLO el token.
    encapsulated.text = b64encode(b"not-a-valid-cms-token").decode("ascii")
    tampered_xml = etree.tostring(root, xml_declaration=False, encoding="UTF-8")

    table.rows[firma_id]["signed_xml_sha256"] = None
    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": tampered_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] is not None
    assert vbody["motivo"].startswith("archive_timestamp_invalido")


# ---------------------------------------------------------------------------
# (l) Binding CA<->tenant con DOS CAs REALES (no un sello autofirmado sin CA,
#     como en el test de aislamiento de arriba): un sello REALMENTE emitido
#     por la CA del tenant A, servido por error junto al ca.crt/crl.der de la
#     CA del tenant B (ambas CAs legítimas, ninguna comprometida) -> la cadena
#     seal->CA sigue fallando, nunca se reporta válido.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xades_lt_seal_issued_by_ca_a_fails_verification_with_ca_b_material(
    signature_env, monkeypatch
) -> None:
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)

    ca_a_key, ca_a_cert = _generate_ca(cn="ca-tenant-a")
    ca_b_key, ca_b_cert = _generate_ca(cn="ca-tenant-b")
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(
        ca_a_key, ca_a_cert, cn="tenant-a-seal"
    )
    # CRL de la CA de OTRO tenant (B) — simula un operador que copió por error
    # el directorio de material CA del tenant equivocado junto al sello REAL
    # del tenant A (a diferencia del test de arriba, aquí el sello SÍ fue
    # emitido por una CA — solo la CA asociada es la incorrecta).
    crl_b_der = _build_crl(ca_b_key, ca_b_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_b_cert), crl_b_der))

    resp = await _sign_indice(client)
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"]

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is False
    assert vbody["motivo"] is not None
    assert vbody["motivo"].startswith("cadena_seal_ca_invalida")


# ---------------------------------------------------------------------------
# OCSP stapled en XAdES-LT (E06, RF-FIR-15) — material de responder OCSP
# delegado, ADITIVO a la CRL ya cubierta por los tests LT/LTA de arriba.
# ---------------------------------------------------------------------------
#
# `settings.signer_ocsp_enabled` es `False` por defecto (kill-switch) — cada
# test que ejercita este camino lo activa explícitamente vía monkeypatch,
# junto a `signer_lt_enabled=True` (OCSP solo tiene efecto DENTRO del bloque
# XAdES-LT). El material CA/CRL/responder se genera enteramente en memoria
# (`_generate_ca`/`_issue_seal_signed_by_ca`/`_build_crl`/
# `_issue_ocsp_responder`) — nunca toca el filesystem ni
# `scripts/gen-dev-ocsp-responder.sh`.


@pytest.mark.asyncio
async def test_ocsp_sign_embeds_ocsp_values_and_archive_timestamp_still_covers(
    signature_env, monkeypatch
) -> None:
    """(a) Con `signer_lt_enabled=True` + `signer_ocsp_enabled=True` y ambos
    materiales (CA/CRL y responder OCSP) montados, el XML firmado lleva
    `xades:OCSPValues` como HIJO de `xades:RevocationValues`, DESPUÉS de
    `xades:CRLValues` — y el `ArchiveTimeStamp` SIGUE cubriendo/validando
    (no se rompe `_archive_ts_covered_bytes` al insertar OCSP antes de él).
    `/verify` reporta `ocsp_present=True`/`ocsp_cert_status='good'` sin
    cambiar `xades_level` (sigue XAdES-LTA)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    ocsp_cert_pem, ocsp_key_pem = _issue_ocsp_responder(ca_key, ca_cert)
    install_seal(
        _lt_ocsp_signer_ref(
            seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der, ocsp_cert_pem, ocsp_key_pem
        )
    )

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["xades_level"] == "XAdES-LTA"
    assert body["ocsp_present"] is True

    signed_xml = uploads[0]["content"]
    root = etree.fromstring(signed_xml)
    ns = {
        "ds": "http://www.w3.org/2000/09/xmldsig#",
        "xades": "http://uri.etsi.org/01903/v1.3.2#",
        "xadesv141": "http://uri.etsi.org/01903/v1.4.1#",
    }
    rev_values = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties/xades:RevocationValues",
        namespaces=ns,
    )
    assert rev_values is not None
    children_tags = [etree.QName(c).localname for c in rev_values]
    # CRLValues PRIMERO, OCSPValues DESPUÉS — mismo RevocationValues, aditivo.
    assert children_tags == ["CRLValues", "OCSPValues"]

    ocsp_encapsulated = rev_values.find(
        "xades:OCSPValues/xades:EncapsulatedOCSPValue", namespaces=ns
    )
    assert ocsp_encapsulated is not None and ocsp_encapsulated.text
    assert len(b64decode(ocsp_encapsulated.text.strip())) > 0

    # El ArchiveTimeStamp sigue presente y su cobertura no se rompió.
    archive_ts = root.find(
        ".//xades:UnsignedProperties/xades:UnsignedSignatureProperties/xadesv141:ArchiveTimeStamp",
        namespaces=ns,
    )
    assert archive_ts is not None

    firma_id = body["firma_id"]
    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["xades_level"] == "XAdES-LTA"  # OCSP no cambia el nivel
    assert vbody["ocsp_present"] is True
    assert vbody["ocsp_cert_status"] == "good"
    assert vbody["motivo"] is None


@pytest.mark.asyncio
async def test_ocsp_retrocompat_lt_signature_without_ocsp_still_verifies(
    signature_env, monkeypatch
) -> None:
    """(b) Retrocompatibilidad: una firma XAdES-LT/LTA SIN OCSPValues (el
    kill-switch `signer_ocsp_enabled=False`, comportamiento previo a este
    incremento) verifica EXACTAMENTE igual, con `ocsp_present=False` — nunca
    un fallo por ausencia."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    # `docker-compose.yml` de desarrollo activa SIGNER_OCSP_ENABLED=true por
    # defecto (ver contrato del incremento) — se fija explícitamente en False
    # aquí para ejercitar el kill-switch real, en vez de asumir el default de
    # código (que solo aplica sin ese override de entorno).
    monkeypatch.setattr(settings, "signer_ocsp_enabled", False)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["xades_level"] == "XAdES-LTA"
    assert body["ocsp_present"] is False

    signed_xml = uploads[0]["content"]
    assert b"OCSPValues" not in signed_xml

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": body["firma_id"], "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["xades_level"] == "XAdES-LTA"
    assert vbody["ocsp_present"] is False
    assert vbody["ocsp_cert_status"] is None


@pytest.mark.asyncio
async def test_ocsp_downgrade_no_responder_material_stays_lta_and_audits(
    signature_env, monkeypatch
) -> None:
    """(c) `signer_ocsp_enabled=True` pero el tenant NO tiene
    ocsp.crt/ocsp.key montado (el caso normal fuera de
    `scripts/gen-dev-ocsp-responder.sh`) -> degradación OBSERVABLE: la firma
    XAdES-LT/LTA se produce IGUAL (nunca falla), sin OCSPValues, y queda una
    entrada de auditoría DEDICADA `firma.ocsp_downgrade_no_responder`."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    # Material LT sin responder OCSP (ocsp_cert_pem/ocsp_key_pem ausentes).
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["xades_level"] == "XAdES-LTA"
    assert body["ocsp_present"] is False

    audit_calls = [
        c for c in conn.execute.call_args_list if "INSERT INTO public.audit_log" in str(c.args[0])
    ]
    actions = [c.args[5] for c in audit_calls]
    assert "firma.ocsp_downgrade_no_responder" in actions
    downgrade_payload = json.loads(
        next(c for c in audit_calls if c.args[5] == "firma.ocsp_downgrade_no_responder").args[8]
    )
    assert downgrade_payload["xades_level"] == "XAdES-LTA"
    assert downgrade_payload["signer_ocsp_enabled"] is True
    assert downgrade_payload["motivo"] == "sin_material_responder_ocsp"


@pytest.mark.asyncio
async def test_ocsp_disabled_kill_switch_never_audits_downgrade(
    signature_env, monkeypatch
) -> None:
    """`signer_ocsp_enabled=False` (default de código) es simplemente el
    feature apagado — NUNCA es una "degradación": no debe emitirse
    `firma.ocsp_downgrade_no_responder`, incluso con LT/LTA activo."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    # `docker-compose.yml` de desarrollo activa SIGNER_OCSP_ENABLED=true por
    # defecto — se fija explícitamente en False para ejercitar el kill-switch
    # real (ver misma nota en test_ocsp_retrocompat_lt_signature_without_ocsp).
    monkeypatch.setattr(settings, "signer_ocsp_enabled", False)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    assert resp.json()["xades_level"] == "XAdES-LTA"

    audit_calls = [
        c for c in conn.execute.call_args_list if "INSERT INTO public.audit_log" in str(c.args[0])
    ]
    actions = [c.args[5] for c in audit_calls]
    assert "firma.ocsp_downgrade_no_responder" not in actions


@pytest.mark.asyncio
async def test_ocsp_require_without_responder_material_returns_503(
    signature_env, monkeypatch
) -> None:
    """(d) `signer_ocsp_require=True` + sin material de responder OCSP -> 503
    `ocsp_material_required`, fail-closed — la fila `firma` NUNCA llega a
    persistirse (mismo trato que `signer_lt_require=True`)."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_require", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    install_seal(_lt_signer_ref(seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der))

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "ocsp_material_required"
    assert table.rows == {}


@pytest.mark.asyncio
async def test_ocsp_require_without_ca_material_returns_503(signature_env, monkeypatch) -> None:
    """(d bis, hallazgo Sec/Baja auditoría OCSP) `signer_ocsp_require=True`
    presupone LT: si el tenant NO tiene NI material de CA local montado
    (``ca_cert_pem``/``crl_der`` ausentes — la firma degradaría a XAdES-T,
    ``lt_downgrade_no_ca``), el 503 `ocsp_material_required` debe dispararse
    IGUAL — antes de esta corrección el `raise` vivía anidado dentro del
    bloque LT y nunca se alcanzaba en este camino, así que un operador con
    OCSP obligatorio pero sin CA obtenía una firma XAdES-T silenciosa en vez
    del fail-closed esperado."""
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_require", True)

    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))  # sin ca_cert_pem/crl_der

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "ocsp_material_required"
    assert table.rows == {}


@pytest.mark.asyncio
async def test_ocsp_revoked_seal_reported_by_verify(signature_env, monkeypatch) -> None:
    """(e) Coherencia CRL<->OCSP: un sello cuyo serial SÍ aparece en la CRL
    ya es rechazado por la comprobación CRL de XAdES-LT
    (`certificado_sello_revocado`, ver test LT existente) ANTES de llegar a
    evaluar OCSP — pero la respuesta OCSP embebida, si se inspeccionara
    fuera de banda, también declararía `REVOKED` (mismo serial, misma CRL).
    Aquí se verifica esto último de forma directa (sin pasar por /verify,
    que nunca llega a evaluar OCSP por el corte temprano en la CRL)."""
    from app.providers.base import PreparedDoc
    from app.providers.local import LocalSigner

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_enabled", True)
    monkeypatch.setattr(settings, "signer_timestamp_enabled", True)

    tsa_cert_pem, tsa_key_pem = _generate_tsa_cert()
    monkeypatch.setattr(
        tsa_module, "load_tsa_seal", lambda: _tsa_signer_ref(tsa_cert_pem, tsa_key_pem)
    )

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert, revoked_serials=(seal_cert.serial_number,))
    ocsp_cert_pem, ocsp_key_pem = _issue_ocsp_responder(ca_key, ca_cert)
    signer_ref = _lt_ocsp_signer_ref(
        seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der, ocsp_cert_pem, ocsp_key_pem
    )

    prepared = PreparedDoc(xml_bytes=INDICE_XML, objeto_tipo="indice", objeto_id="exp-1")
    signed = await LocalSigner().sign(prepared, signer_ref)

    assert signed.extra["ocsp_present"] is True
    assert signed.extra["ocsp_cert_status"] == "revoked"


@pytest.mark.asyncio
async def test_ocsp_verify_pins_responder_fingerprint(monkeypatch) -> None:
    """(f) Pinning (mismo patrón que
    `test_xades_lt_verify_pins_ca_fingerprint`): sin
    `expected_ocsp_responder_fingerprint` (fila legacy/sin OCSP) o con uno
    que no coincide con el responder REALMENTE embebido, `verify_xades`
    falla-cerrado. Llamada directa a la función (no vía HTTP)."""
    from app.providers.base import PreparedDoc
    from app.providers.local import LocalSigner, verify_xades

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    monkeypatch.setattr(settings, "signer_ocsp_enabled", True)
    monkeypatch.setattr(settings, "signer_timestamp_enabled", True)

    ca_key, ca_cert = _generate_ca()
    seal_cert_pem, seal_key_pem, seal_cert = _issue_seal_signed_by_ca(ca_key, ca_cert)
    crl_der = _build_crl(ca_key, ca_cert)
    ocsp_cert_pem, ocsp_key_pem = _issue_ocsp_responder(ca_key, ca_cert)

    tsa_cert_pem, tsa_key_pem = _generate_tsa_cert()
    monkeypatch.setattr(
        tsa_module, "load_tsa_seal", lambda: _tsa_signer_ref(tsa_cert_pem, tsa_key_pem)
    )

    prepared = PreparedDoc(xml_bytes=INDICE_XML, objeto_tipo="indice", objeto_id="exp-1")
    signer_ref = _lt_ocsp_signer_ref(
        seal_cert_pem, seal_key_pem, _ca_cert_pem(ca_cert), crl_der, ocsp_cert_pem, ocsp_key_pem
    )
    signed = await LocalSigner().sign(prepared, signer_ref)
    assert signed.extra["ocsp_present"] is True

    fp = cert_fingerprint(seal_cert_pem)
    correct_ocsp_fp = signed.extra["ocsp_responder_fingerprint"]

    outcome_ok = verify_xades(
        signed.signed_xml,
        fp,
        expected_tsa_fingerprint=signed.extra["tsa_cert_fingerprint"],
        expected_ca_fingerprint=signed.extra["ca_fingerprint"],
        expected_ocsp_responder_fingerprint=correct_ocsp_fp,
    )
    assert outcome_ok.valida is True
    assert outcome_ok.ocsp_present is True
    assert outcome_ok.ocsp_cert_status == "good"

    outcome_missing = verify_xades(
        signed.signed_xml,
        fp,
        expected_tsa_fingerprint=signed.extra["tsa_cert_fingerprint"],
        expected_ca_fingerprint=signed.extra["ca_fingerprint"],
        expected_ocsp_responder_fingerprint=None,
    )
    assert outcome_missing.valida is False
    assert outcome_missing.motivo == "ocsp_invalido: ocsp_responder_fingerprint_no_registrado"

    outcome_mismatch = verify_xades(
        signed.signed_xml,
        fp,
        expected_tsa_fingerprint=signed.extra["tsa_cert_fingerprint"],
        expected_ca_fingerprint=signed.extra["ca_fingerprint"],
        expected_ocsp_responder_fingerprint="0" * 64,
    )
    assert outcome_mismatch.valida is False
    assert outcome_mismatch.motivo == "ocsp_invalido: ocsp_responder_fingerprint_no_coincide"


@pytest.mark.asyncio
async def test_verify_declara_advertencias_de_un_sello_sin_esscertid_anterior_al_corte(
    signature_env, monkeypatch
) -> None:
    """AREA-04-09: `/verify` publica `advertencias[]` sobre una firma que VALIDA.

    Un sello anterior a `tsa_esscertid_corte` sin ESSCertID sigue valiendo, pero el
    verificador lo declara. Aquí se fuerza el aviso en la salida del verificador de
    tokens (la mecánica del token legado la prueba test_tsa_gentime_y_esscertid)
    para comprobar el CABLEADO hasta la respuesta HTTP; y que un sello normal no
    lleva ninguno.
    """
    client, conn, table, uploads, install_seal, install_tsa_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))
    resp = await _sign_indice(client)
    assert resp.status_code == 201
    firma_id = resp.json()["firma_id"]
    signed_xml = uploads[0]["content"].decode()
    peticion = {"firma_id": firma_id, "payload_xml": signed_xml}

    normal = (await client.post("/api/v1/signature/verify", json=peticion, headers=TENANT_HEADERS)).json()
    assert normal["valida"] is True
    assert normal["advertencias"] == []

    real = tsa_module.verify_timestamp_token

    def _con_aviso(*a, **kw):  # noqa: ANN002, ANN003
        r = real(*a, **kw)
        r.advertencias.append(tsa_module.ADVERTENCIA_SIN_ESSCERTID)
        return r

    monkeypatch.setattr("app.providers.local.tsa.verify_timestamp_token", _con_aviso)
    avisado = (await client.post("/api/v1/signature/verify", json=peticion, headers=TENANT_HEADERS)).json()
    assert avisado["valida"] is True
    assert avisado["advertencias"] == ["tsa_sin_esscertid_anterior_al_corte"]
