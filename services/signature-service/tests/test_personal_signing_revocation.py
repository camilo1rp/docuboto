# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests — anclaje de la sub-CA de usuarios + revocación en línea de la firma
personal PKI (E17/F4, Incremento 2, Baja #4).

Dos niveles:

  - Proveedor (``LocalSigner.sign``/``verify_xades``): cripto REAL, en
    memoria — construye una sub-CA de test (``basicConstraints CA:TRUE`` +
    ``keyUsage keyCertSign``, mismo espíritu que ``scripts/gen-user-cert.sh``)
    y un leaf emitido por ella, ejercitando el camino EXACTO que
    ``cadena_service._firmar_personal``/``auth-service`` producirían.
  - Router (``POST /verify``): ``get_signing_key_revocation_status_remote``
    mockeado (nunca httpx real) — mismo patrón que
    ``tests/test_cadena_personal_signing.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.core.http_clients import SigningKeyRevocationStatusUnavailableError
from app.providers.base import PreparedDoc, SignerRef
from app.providers.local import LocalSigner, verify_xades

# ---------------------------------------------------------------------------
# Helpers — cripto efímera en memoria (nunca toca el filesystem/openssl)
# ---------------------------------------------------------------------------


def _generate_sub_ca(cn: str = "ca-usuarios-test") -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Sub-CA de usuarios de test — ``basicConstraints CA:TRUE`` +
    ``keyUsage keyCertSign`` (exigidos por la rama personal de
    ``verify_xades``), mismo espíritu que ``scripts/gen-user-cert.sh``."""
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


def _issue_user_cert(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate, cn: str = "OPERADOR"
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
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
    return key, cert


def _generate_tsa_cert(cn: str = "orpycamcp-dev-tsa") -> tuple[bytes, bytes]:
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
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@pytest.fixture(autouse=True)
def _install_local_tsa(monkeypatch: pytest.MonkeyPatch) -> None:
    """La rama de anclaje personal solo se ejercita bajo XAdES-T (mismo
    prerrequisito que el bloque LT institucional, ver
    ``app.providers.local.LocalSigner.sign``) — instala una TSA efímera en
    memoria, TAL COMO ``tests/test_sign_xml_xades.py::signature_env`` hace
    por defecto, para que ``settings.signer_timestamp_enabled`` (True por
    defecto) produzca XAdES-T sin depender de material montado en disco."""
    cert_pem, key_pem = _generate_tsa_cert()
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=cert_pem, key_pem=key_pem, key_password=None),
    )


async def _sign_personal(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    user_key: rsa.RSAPrivateKey,
    user_cert: x509.Certificate,
    *,
    xml_bytes: bytes = (
        b'<acto xmlns="urn:orpyca:firma-personal" objetoTipo="radicado" '
        b'objetoId="x">contenido-de-prueba</acto>'
    ),
) -> tuple[bytes, object]:
    """Firma PERSONAL vía ``LocalSigner`` con ``SignerRef.ca_cert_pem``
    poblado — MISMA técnica que ``cadena_service._firmar_personal`` produce
    tras consumir ``ca_cert_pem`` de ``sign-digest`` (auth-service)."""

    async def _remote_sign(digest: bytes) -> bytes:
        return user_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    signer_ref = SignerRef(
        cert_pem=user_cert.public_bytes(serialization.Encoding.PEM),
        key_pem=b"",
        remote_sign=_remote_sign,
        tipo_firma="personal",
        nivel_conformidad="xades_personal_no_acreditado",
        acreditado=False,
        ca_cert_pem=ca_cert.public_bytes(serialization.Encoding.PEM),
    )
    prepared = PreparedDoc(xml_bytes=xml_bytes, objeto_tipo="radicado", objeto_id="x")
    signed = await LocalSigner().sign(prepared, signer_ref)
    return signed.signed_xml, signed


# ---------------------------------------------------------------------------
# Proveedor — smoke/round-trip (contrato: "emite un leaf con una sub-CA de
# test en memoria, firma, embebe la sub-CA, verifica cadena+pin OK")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_signature_embeds_sub_ca_certificate_values() -> None:
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)

    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    assert signed.provider == "xades_personal"
    assert b"CertificateValues" in signed_xml
    # SIN el resto del camino LT — la revocación personal es en línea.
    assert b"RevocationValues" not in signed_xml
    assert b"ArchiveTimeStamp" not in signed_xml
    assert signed.extra.get("ca_fingerprint") == ca_cert.fingerprint(hashes.SHA256()).hex()


@pytest.mark.asyncio
async def test_verify_xades_personal_branch_validates_chain_and_pin() -> None:
    """Rama personal de ``verify_xades``: cadena leaf→sub-CA + pin del
    fingerprint. PUNTO DE MÁXIMO RIESGO cubierto: si esta firma cayera en el
    camino LT institucional (que exige ``RevocationValues``), ``valida``
    sería ``False`` con ``motivo="revocation_values_ausente"`` — el test
    confirma que NO es así."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    leaf_fp = user_cert.fingerprint(hashes.SHA256()).hex()
    ca_fp = ca_cert.fingerprint(hashes.SHA256()).hex()

    outcome = verify_xades(
        signed_xml,
        leaf_fp,
        expected_tsa_fingerprint=signed.extra.get("tsa_cert_fingerprint"),
        expected_user_ca_fingerprint=ca_fp,
    )
    assert outcome.valida is True
    assert outcome.motivo is None
    assert outcome.xades_level == "XAdES-T"
    assert signed.certificado_fingerprint == leaf_fp


@pytest.mark.asyncio
async def test_verify_xades_personal_branch_rejects_wrong_pin() -> None:
    """La huella de la sub-CA embebida NO coincide con
    ``expected_user_ca_fingerprint`` (``firma.ca_fingerprint`` persistido) —
    fail-closed, nunca confía en el material embebido sin cotejarlo."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    leaf_fp = user_cert.fingerprint(hashes.SHA256()).hex()
    outcome = verify_xades(
        signed_xml,
        leaf_fp,
        expected_tsa_fingerprint=signed.extra.get("tsa_cert_fingerprint"),
        expected_user_ca_fingerprint="0" * 64,
    )
    assert outcome.valida is False
    assert outcome.motivo == "user_ca_fingerprint_no_coincide"


@pytest.mark.asyncio
async def test_verify_xades_personal_branch_leaf_from_foreign_ca_fails_anchor() -> None:
    """Contrato: "leaf de OTRA CA -> falla el anclaje". El leaf fue REALMENTE
    emitido por ``ca_cert`` (la sub-CA correcta), pero el material embebido
    en ``CertificateValues``/el pin de verificación apuntan a
    ``foreign_ca_cert`` (OTRA CA, con su propia huella coincidente) — la
    cadena ``leaf.verify_directly_issued_by(foreign_ca_cert)`` DEBE fallar
    aunque el pin de huella "coincida" (simula un fingerprint persistido
    apuntando a la CA equivocada, o un XML manipulado): fail-closed en la
    verificación de firma, no solo en el pin."""
    ca_key, ca_cert = _generate_sub_ca(cn="ca-correcta")
    foreign_ca_key, foreign_ca_cert = _generate_sub_ca(cn="ca-ajena")
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)  # emitido por la CA CORRECTA

    # Firma normal (embebe ca_cert, la CA que REALMENTE emitió el leaf)...
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)
    leaf_fp = user_cert.fingerprint(hashes.SHA256()).hex()

    # ...pero se verifica pineando la CA AJENA (simulando que
    # firma.ca_fingerprint quedó mal persistido, o que el atacante forjó
    # CertificateValues con una CA suya cuya huella intenta hacer pasar).
    # El pin de huella por sí solo NO basta — la cadena debe fallar.
    foreign_fp = foreign_ca_cert.fingerprint(hashes.SHA256()).hex()
    outcome = verify_xades(
        signed_xml,
        leaf_fp,
        expected_tsa_fingerprint=signed.extra.get("tsa_cert_fingerprint"),
        expected_user_ca_fingerprint=foreign_fp,
    )
    assert outcome.valida is False
    # Falla en el pin (la CA embebida es `ca_cert`, no `foreign_ca_cert`) —
    # ver el siguiente test para el caso donde el pin SÍ coincide pero la
    # cadena de firma real no verifica.
    assert outcome.motivo == "user_ca_fingerprint_no_coincide"

    # Ahora el caso MÁS FUERTE: el proveedor embebe DIRECTAMENTE la CA ajena
    # (simulando un signer_ref mal poblado o un XML forjado con una CA cuyo
    # fingerprint SÍ coincide con lo persistido) — la cadena real
    # leaf->foreign_ca_cert nunca puede verificar (el leaf no fue firmado
    # por foreign_ca_key).
    forged_xml, forged_signed = await _sign_personal(
        foreign_ca_key, foreign_ca_cert, user_key, user_cert
    )
    outcome2 = verify_xades(
        forged_xml,
        leaf_fp,
        expected_tsa_fingerprint=forged_signed.extra.get("tsa_cert_fingerprint"),
        expected_user_ca_fingerprint=foreign_fp,
    )
    assert outcome2.valida is False
    assert outcome2.motivo is not None and outcome2.motivo.startswith("cadena_personal_ca_invalida")


@pytest.mark.asyncio
async def test_verify_xades_personal_branch_rejects_ca_without_basic_constraints() -> None:
    """La sub-CA embebida no es realmente una CA (``basicConstraints
    CA:TRUE`` ausente/False) — fail-closed, nunca se confía en un
    certificado hoja como si fuera una CA."""
    # "sub-CA" sin basicConstraints CA:TRUE — en realidad un leaf cualquiera.
    fake_ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    fake_ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "no-es-una-ca")])
    fake_ca_cert = (
        x509.CertificateBuilder()
        .subject_name(fake_ca_name)
        .issuer_name(fake_ca_name)
        .public_key(fake_ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .sign(fake_ca_key, hashes.SHA256())
    )
    user_key, user_cert = _issue_user_cert(fake_ca_key, fake_ca_cert)
    signed_xml, signed = await _sign_personal(fake_ca_key, fake_ca_cert, user_key, user_cert)

    leaf_fp = user_cert.fingerprint(hashes.SHA256()).hex()
    ca_fp = fake_ca_cert.fingerprint(hashes.SHA256()).hex()
    outcome = verify_xades(
        signed_xml,
        leaf_fp,
        expected_tsa_fingerprint=signed.extra.get("tsa_cert_fingerprint"),
        expected_user_ca_fingerprint=ca_fp,
    )
    assert outcome.valida is False
    assert outcome.motivo == "user_ca_sin_basic_constraints"


@pytest.mark.asyncio
async def test_verify_xades_personal_grandfather_inc1_signature_unaffected() -> None:
    """Retrocompat exacta (Sección D del contrato): una firma
    ``xades_personal`` SIN ``CertificateValues`` (Incremento 1, o cualquier
    firma personal sin sub-CA anclada) sigue verificando como XAdES-B/T de
    siempre, SIN activar la rama personal — ``expected_user_ca_fingerprint``
    se pasa (como lo haría el router para CUALQUIER firma xades_personal, ver
    ``app.routers.signature.verify``) pero la ausencia de CertificateValues
    en el XML hace que ``verify_xades`` retorne ANTES de siquiera mirar ese
    parámetro."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)

    async def _remote_sign(digest: bytes) -> bytes:
        return user_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    # SignerRef SIN ca_cert_pem — exactamente como Incremento 1 (grandfather).
    signer_ref = SignerRef(
        cert_pem=user_cert.public_bytes(serialization.Encoding.PEM),
        key_pem=b"",
        remote_sign=_remote_sign,
        tipo_firma="personal",
        nivel_conformidad="xades_personal_no_acreditado",
        acreditado=False,
    )
    prepared = PreparedDoc(
        xml_bytes=b'<acto xmlns="urn:orpyca:firma-personal" objetoTipo="radicado" '
        b'objetoId="x">contenido</acto>',
        objeto_tipo="radicado",
        objeto_id="x",
    )
    signed = await LocalSigner().sign(prepared, signer_ref)
    assert b"CertificateValues" not in signed.signed_xml
    assert signed.extra.get("ca_fingerprint") is None

    leaf_fp = user_cert.fingerprint(hashes.SHA256()).hex()
    # Un fingerprint "esperado" arbitrario NUNCA importa aquí — la firma no
    # lleva CertificateValues, así que verify_xades ni lo evalúa.
    outcome = verify_xades(
        signed.signed_xml,
        leaf_fp,
        expected_tsa_fingerprint=signed.extra.get("tsa_cert_fingerprint"),
        expected_user_ca_fingerprint="f" * 64,
    )
    assert outcome.valida is True
    assert outcome.motivo is None
    assert outcome.xades_level == "XAdES-T"


# ---------------------------------------------------------------------------
# Router — POST /verify + revocación en línea (A2)
# ---------------------------------------------------------------------------


def _firma_row(
    *,
    firma_id: uuid.UUID,
    signed,
    ca_fingerprint: str | None,
    sello_tiempo: datetime | None = None,
) -> dict:
    return {
        "id": firma_id,
        "objeto_tipo": "radicado",
        "objeto_id": "x",
        "formato_solicitado": None,
        "firmante_id": uuid.uuid4(),
        "payload_sha256": signed.payload_sha256,
        "firma_blob": signed.signature_value_b64,
        "provider": "xades_personal",
        "sello_tiempo": sello_tiempo or datetime.now(UTC),
        "created_at": datetime.now(UTC),
        "certificado_pem": signed.certificado_pem,
        "minio_key_firmado": None,
        "nivel_conformidad": signed.nivel_conformidad,
        "xades_level": signed.xades_level,
        "tipo_firma": "personal",
        "certificado_fingerprint": signed.certificado_fingerprint,
        "signed_xml_sha256": None,
        "tsa_token": None,
        "tsa_timestamp": None,
        "tsa_cert_fingerprint": signed.extra.get("tsa_cert_fingerprint"),
        "tsa_acreditado": False,
        "ca_fingerprint": ca_fingerprint,
        "ocsp_responder_fingerprint": None,
    }


@pytest.mark.asyncio
async def test_verify_reports_revocado_after_revocation(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(
        firma_id=firma_id, signed=signed, ca_fingerprint=signed.extra.get("ca_fingerprint")
    )
    # Revocada ANTES de firmar (la clave ya estaba revocada cuando se sello):
    # AREA-04-13 — solo una revocación POSTERIOR al instante de la firma la deja
    # intacta; esta no.
    revoked_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    mock_revocation = AsyncMock(return_value={"status": "revocado", "revocado_at": revoked_at})
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote", mock_revocation
    )

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Cripto SIGUE siendo válida — nunca difamar la cripto por revocación.
    assert body["firma_criptograficamente_valida"] is True
    assert body["revocacion_status"] == "revocado"
    assert body["valida"] is False
    assert body["motivo"] == "certificado_revocado"
    assert body["revocacion_posterior"] is None
    mock_revocation.assert_awaited_once()
    assert mock_revocation.await_args.args[0] == signed.certificado_fingerprint


@pytest.mark.asyncio
async def test_revocacion_posterior_no_invalida_firma_previa(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AREA-04-13: la clave se revocó DESPUÉS de firmar -> la firma sigue siendo
    válida y se informa ``revocacion_posterior``. El sello de la TSA (verificado)
    es el instante de la firma."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(
        firma_id=firma_id, signed=signed, ca_fingerprint=signed.extra.get("ca_fingerprint")
    )
    revoked_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote",
        AsyncMock(return_value={"status": "revocado", "revocado_at": revoked_at}),
    )

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valida"] is True
    assert body["motivo"] is None
    assert body["revocacion_status"] == "revocado"
    assert body["revocacion_posterior"] is True
    assert body["firma_criptograficamente_valida"] is True


async def _verify_revocada(client_with_conn, monkeypatch, *, revocado: dict, sello_tiempo=None):
    """Firma personal (con la TSA local activa por defecto) y la verifica contra
    un `revocation-status` simulado. Devuelve el cuerpo JSON de /verify."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)
    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(
        firma_id=firma_id,
        signed=signed,
        ca_fingerprint=signed.extra.get("ca_fingerprint"),
        sello_tiempo=sello_tiempo,
    )
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote",
        AsyncMock(return_value={"status": "revocado", **revocado}),
    )
    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_revocacion_se_compara_con_el_genTime_de_la_tsa_no_con_now(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con TSA verificada el instante es su genTime (~ahora). Revocada mañana:
    posterior. Revocada hace 15 días: anterior -> invalida. Comparar contra
    `now()` en vez del genTime tumba el primer caso."""
    body = await _verify_revocada(
        client_with_conn,
        monkeypatch,
        revocado={"revocado_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    )
    assert body["valida"] is True
    assert body["revocacion_posterior"] is True

    body = await _verify_revocada(
        client_with_conn,
        monkeypatch,
        revocado={"revocado_at": (datetime.now(UTC) - timedelta(days=15)).isoformat()},
    )
    assert body["valida"] is False
    assert body["motivo"] == "certificado_revocado"


@pytest.mark.asyncio
async def test_sin_tsa_verificada_el_instante_no_es_cierto_y_no_valida(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El `sello_tiempo` de la fila lo pone el servidor: NO es fecha cierta.
    Sin TSA verificada, una revocación 'posterior' según ese sello deja la firma
    sin validar y con `revocacion_posterior=None` (AREA-04-13, hallazgo Medio)."""
    from app.core.config import settings as signature_settings

    monkeypatch.setattr(signature_settings, "signer_timestamp_enabled", False)
    body = await _verify_revocada(
        client_with_conn,
        monkeypatch,
        revocado={"revocado_at": (datetime.now(UTC) - timedelta(days=5)).isoformat()},
        sello_tiempo=datetime.now(UTC) - timedelta(days=10),
    )
    assert body["valida"] is False
    assert body["motivo"] == "instante_firma_no_cierto"
    assert body["revocacion_posterior"] is None
    assert body["firma_criptograficamente_valida"] is True


@pytest.mark.asyncio
async def test_revocacion_por_compromiso_invalida_hacia_atras(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`key_compromise` invalida aun siendo posterior a una TSA verificada
    (ETSI EN 319 102-1); cualquier otro motivo cerrado (`superseded`) no."""
    futuro = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    body = await _verify_revocada(
        client_with_conn,
        monkeypatch,
        revocado={"revocado_at": futuro, "motivo_revocacion": "key_compromise"},
    )
    assert body["valida"] is False
    assert body["motivo"] == "clave_comprometida"
    assert body["revocacion_posterior"] is None

    body = await _verify_revocada(
        client_with_conn,
        monkeypatch,
        revocado={"revocado_at": futuro, "motivo_revocacion": "superseded"},
    )
    assert body["valida"] is True
    assert body["revocacion_posterior"] is True


@pytest.mark.asyncio
async def test_revocada_sin_fecha_legible_invalida_fail_closed(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin ``revocado_at`` no hay forma de probar que fue posterior: invalida."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)
    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(
        firma_id=firma_id, signed=signed, ca_fingerprint=signed.extra.get("ca_fingerprint")
    )
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote",
        AsyncMock(return_value={"status": "revocado", "revocado_at": None}),
    )
    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )
    assert resp.json()["valida"] is False
    assert resp.json()["motivo"] == "certificado_revocado"


@pytest.mark.asyncio
async def test_verify_reports_revocado_for_grandfathered_inc1_signature(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La revocación cubre TAMBIÉN una firma "Inc.1" (sin CertificateValues,
    ``ca_fingerprint=None``) — el lookup es por ``certificado_fingerprint``
    (siempre poblado desde Incremento 1), no por el ancla de la sub-CA."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)

    async def _remote_sign(digest: bytes) -> bytes:
        return user_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    signer_ref = SignerRef(
        cert_pem=user_cert.public_bytes(serialization.Encoding.PEM),
        key_pem=b"",
        remote_sign=_remote_sign,
        tipo_firma="personal",
        nivel_conformidad="xades_personal_no_acreditado",
        acreditado=False,
    )  # SIN ca_cert_pem — grandfather Inc.1
    prepared = PreparedDoc(
        xml_bytes=b'<acto xmlns="urn:orpyca:firma-personal" objetoTipo="radicado" '
        b'objetoId="x">contenido</acto>',
        objeto_tipo="radicado",
        objeto_id="x",
    )
    signed = await LocalSigner().sign(prepared, signer_ref)

    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(firma_id=firma_id, signed=signed, ca_fingerprint=None)
    mock_revocation = AsyncMock(return_value={"status": "revocado"})
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote", mock_revocation
    )

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed.signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["firma_criptograficamente_valida"] is True
    assert body["valida"] is False
    assert body["revocacion_status"] == "revocado"


@pytest.mark.asyncio
async def test_verify_revocation_channel_down_fails_closed_by_default(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(
        firma_id=firma_id, signed=signed, ca_fingerprint=signed.extra.get("ca_fingerprint")
    )
    mock_revocation = AsyncMock(
        side_effect=SigningKeyRevocationStatusUnavailableError("auth-service inalcanzable")
    )
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote", mock_revocation
    )
    assert settings.verify_personal_revocation_require is True  # default

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Canal caído -> valida=False, motivo explícito, pero NUNCA difama la cripto.
    assert body["valida"] is False
    assert body["motivo"] == "revocacion_no_verificable"
    assert body["firma_criptograficamente_valida"] is True
    assert body["revocacion_status"] == "no_verificable"


@pytest.mark.asyncio
async def test_verify_revocation_channel_down_fails_open_when_disabled(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "verify_personal_revocation_require", False)

    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(
        firma_id=firma_id, signed=signed, ca_fingerprint=signed.extra.get("ca_fingerprint")
    )
    mock_revocation = AsyncMock(
        side_effect=SigningKeyRevocationStatusUnavailableError("auth-service inalcanzable")
    )
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote", mock_revocation
    )

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Opt-in fail-open: la cripto ya validó, así que `valida` se mantiene.
    assert body["valida"] is True
    assert body["firma_criptograficamente_valida"] is True
    assert body["revocacion_status"] == "no_verificable"


@pytest.mark.asyncio
async def test_verify_revocation_desconocido_never_reports_vigente(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: huella no encontrada (otro tenant / forjada / titular
    borrado por CASCADE) -> ``desconocido``, NUNCA ``vigente``."""
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key, ca_cert)
    signed_xml, signed = await _sign_personal(ca_key, ca_cert, user_key, user_cert)

    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    conn.fetchrow.return_value = _firma_row(
        firma_id=firma_id, signed=signed, ca_fingerprint=signed.extra.get("ca_fingerprint")
    )
    mock_revocation = AsyncMock(return_value={"status": "desconocido"})
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote", mock_revocation
    )

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers={"X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valida"] is False
    assert body["revocacion_status"] == "desconocido"
    assert body["motivo"] == "certificado_desconocido"


@pytest.mark.asyncio
async def test_verify_native_hmac_never_calls_revocation_status(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrocompat HMAC (Sección D): ``provider='nativa'`` nunca toca la
    dimensión de revocación — ni siquiera se consulta el canal."""
    from app.core.config import settings as signature_settings
    from app.services import signer as native_signer

    sello_dt = datetime(2024, 1, 1, tzinfo=UTC)
    r = native_signer.firmar(
        b"<indice/>", "11111111-1111-1111-1111-111111111111", sello_dt.isoformat(),
        signature_settings.signature_secret,
        tenant_slug="demo", objeto_tipo="indice", objeto_id="exp-1",
    )
    client, conn = client_with_conn
    conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "objeto_tipo": "indice",
        "objeto_id": "exp-1",
        "formato_solicitado": None,
        "firmante_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "payload_sha256": r["payload_sha256"],
        "firma_blob": r["firma_blob"],
        "provider": r["provider"],
        "sello_tiempo": sello_dt,
        "created_at": sello_dt,
        "key_version": r["key_version"],
    }
    mock_revocation = AsyncMock(return_value={"status": "vigente"})
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote", mock_revocation
    )

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(uuid.uuid4()), "payload_xml": "<indice/>"},
        headers={"X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valida"] is True
    assert body["revocacion_status"] is None
    assert body["firma_criptograficamente_valida"] is True
    mock_revocation.assert_not_awaited()
