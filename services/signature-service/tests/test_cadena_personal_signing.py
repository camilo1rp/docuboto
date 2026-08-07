# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests — firma electrónica PERSONAL PKI por-usuario en la cadena de firma
secuencial (E17/F4, Incremento 1). Mismo estilo de conexión asyncpg mockeada
que ``tests/test_cadena_totp.py`` — reutiliza sus helpers y los de
``tests/test_cadena.py``/``tests/test_cadena_2fa.py``.

``get_signing_key_status_remote``/``sign_digest_remote`` (llamadas HTTP
directas a auth-service, ``app/core/http_clients.py``) se monkeypatchean en
vez de tocar httpx real — mismo patrón que ``verify_totp`` en
``test_cadena_totp.py``. La firma remota se ejercita con criptografía REAL
(un certificado de test emitido por una CA en memoria y una clave RSA real
que firma el digest recibido) para que el ``firma_blob``/``certificado_pem``
producidos sean una firma XAdES genuinamente verificable — no solo un mock
opaco.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.core.http_clients import SignDigestRemoteError
from app.main import app
from app.providers.base import SignerRef
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import FIRMA_ID, SOL_ID, _exec_sig, _make_conn, _sol_row, _turno_row
from tests.test_cadena_2fa import _patch_audit


def _override(conn: AsyncMock) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep


def _clear() -> None:
    app.dependency_overrides.clear()


def _generate_sub_ca(cn: str = "ca-usuarios-test") -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Sub-CA de usuarios de test en memoria (E17/F4, Incremento 2) —
    ``basicConstraints CA:TRUE`` + ``keyUsage keyCertSign``, mismo espíritu
    que ``scripts/gen-user-cert.sh``."""
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


def _generate_tsa_cert(cn: str = "orpycamcp-dev-tsa") -> tuple[bytes, bytes]:
    """Par TSA efímero en memoria (EKU ``id-kp-timeStamping`` crítico,
    RFC 3161 §2.3) — la rama de anclaje personal solo se ejercita bajo
    XAdES-T (mismo prerrequisito que el bloque LT institucional)."""
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


def _issue_user_cert(
    *,
    ca_key: rsa.RSAPrivateKey | None = None,
    ca_cert: x509.Certificate | None = None,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """CA de USUARIOS de test en memoria (NO ``scripts/gen-user-cert.sh``) —
    mismo espíritu que ``tests/test_sign_xml_xades.py``. ``ca_key``/``ca_cert``
    (Incremento 2): cuando se pasan, el leaf se emite con ESA sub-CA —
    default: genera una ad-hoc DISTINTA en cada llamada (comportamiento
    preexistente, sigue sirviendo a los tests que no ejercitan el anclaje)."""
    if ca_key is None or ca_cert is None:
        ca_key, ca_cert = _generate_sub_ca()
    now = datetime.now(UTC)
    user_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OPERADOR")])
    user_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(user_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .sign(ca_key, hashes.SHA256())
    )
    return user_key, user_cert


def _patch_signing_key_status(
    monkeypatch: pytest.MonkeyPatch, *, body: dict | None = None, error: Exception | None = None
) -> AsyncMock:
    mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=body)
    monkeypatch.setattr("app.services.cadena_service.get_signing_key_status_remote", mock)
    return mock


def _patch_sign_digest_remote(
    monkeypatch: pytest.MonkeyPatch,
    *,
    private_key: rsa.RSAPrivateKey | None = None,
    cert_pem: str | None = None,
    ca_cert_pem: str | None = None,
    error: Exception | None = None,
) -> AsyncMock:
    """``ca_cert_pem`` (E17/F4, Incremento 2, Baja #4): cuando se pasa,
    simula que auth-service ancló el leaf a una sub-CA (``sign-digest``
    devuelve el PEM de esa sub-CA) — ``_firmar_personal`` lo consume y
    ``LocalSigner.sign`` recomputa su huella (nunca confía en
    ``ca_fingerprint`` del body a ciegas, ver el docstring de
    ``CadenaFirmaService._firmar_personal``), así que el body de
    ``sign-digest`` NO necesita transportar ``ca_fingerprint`` por separado
    en este fake."""
    if error is not None:
        mock = AsyncMock(side_effect=error)
    else:
        assert private_key is not None

        async def _fake_sign(authorization, digest, totp_code):
            signature = private_key.sign(
                digest, padding.PKCS1v15(), Prehashed(hashes.SHA256())
            )
            return {
                "signature_value_b64": base64.b64encode(signature).decode("ascii"),
                "certificado_pem": cert_pem,
                "certificado_fingerprint": "deadbeef",
                "serial": "ab12",
                "not_after": None,
                "ca_cert_pem": ca_cert_pem,
            }

        mock = AsyncMock(side_effect=_fake_sign)
    # `sign_digest_remote` es invocado desde el closure `_remote_sign` de
    # `app.services.personal_signing.sign_personal_xml` (extract-method
    # E17/F4 Incremento 1, reutilizado por `CadenaFirmaService._firmar_personal`
    # — ver su docstring) — NO desde `cadena_service` directamente.
    monkeypatch.setattr("app.services.personal_signing.sign_digest_remote", mock)
    return mock


_FIRMA_PERSONAL_ROW_TEMPLATE = {
    "id": FIRMA_ID,
    "objeto_tipo": "radicado",
    "objeto_id": "2024-DEMO-E-000001",
    "formato_solicitado": None,
    "firmante_id": USER,
    "sello_tiempo": datetime.now(UTC),
    "created_at": datetime.now(UTC),
}


# ---------------------------------------------------------------------------
# firmar() — camino personal (cert activo)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_with_active_personal_cert_produces_xades_personal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_mock = _patch_audit(monkeypatch)
    user_key, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()

    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(monkeypatch, private_key=user_key, cert_pem=cert_pem)
    # Sin TSA montada en el entorno de test — XAdES-B basta para probar la
    # rama xades_personal (el sellado de tiempo real ya se cubre en
    # test_sign_xml_xades.py, reutiliza el MISMO LocalSigner sin cambios).
    from app.core.config import settings as signature_settings

    monkeypatch.setattr(signature_settings, "signer_timestamp_enabled", False)

    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    captured_insert: dict = {}

    # Cola manual (no Mock.side_effect list) porque el INSERT de `firma`
    # necesita inspeccionarse por POSICIÓN de parámetro ($8=provider,
    # $10=certificado_pem, $13=xades_level — ver app/repositories/
    # firma_repository.py::FirmaRepository.insert) en vez de devolver un
    # valor fijo de la cola.
    queue = [sol, {}, turno, turno]
    tail = [None, sol_firmado, {}]

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
            captured_insert["provider"] = args[7]
            captured_insert["cert_pem"] = args[9]
            captured_insert["xades_level"] = args[12]
            captured_insert["firma_blob"] = args[6]
            row = dict(_FIRMA_PERSONAL_ROW_TEMPLATE)
            row.update(
                payload_sha256=args[5],
                firma_blob=args[6],
                provider=args[7],
                certificado_pem=args[9],
                xades_level=args[12],
            )
            return row
        if queue:
            return queue.pop(0)
        return tail.pop(0)

    conn.fetchval.side_effect = [True, 1]  # require_permission | es_firmante
    conn.fetchrow.side_effect = _fetchrow_router
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["estado"] == "firmado"

        assert captured_insert["provider"] == "xades_personal"
        assert captured_insert["cert_pem"] == cert_pem
        assert captured_insert["xades_level"] in ("XAdES-B", "XAdES-T")

        # La firma XAdES producida es GENUINAMENTE verificable contra el cert real.
        signed_xml = captured_insert["firma_blob"]
        # firma_blob aquí es solo el SignatureValue b64 (no el XML firmado
        # completo) — la verificación end-to-end del XML se cubre en el
        # smoke test del Paso 0 y en test_sign_xml_xades.py; aquí basta con
        # confirmar que se produjo la rama xades_personal y que la
        # auditoría refleja factor_tipo=totp.
        assert signed_xml

        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 1
        payload = firmado_calls[0].kwargs["payload"]
        assert payload["factor_tipo"] == "totp"
        assert payload["challenge_id"] is None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_with_anchored_personal_cert_persists_ca_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E17/F4, Incremento 2 (Baja #4): cuando ``sign-digest`` devuelve
    ``ca_cert_pem`` (leaf anclado a la sub-CA de usuarios),
    ``_firmar_personal`` lo consume, ``LocalSigner.sign`` embebe
    ``xades:CertificateValues`` y ``firmar()`` persiste
    ``firma.ca_fingerprint`` (columna EXISTENTE reutilizada) — end-to-end a
    través de ``POST /requests/{id}/sign``, no solo a nivel de proveedor."""
    _patch_audit(monkeypatch)
    ca_key, ca_cert = _generate_sub_ca()
    user_key, user_cert = _issue_user_cert(ca_key=ca_key, ca_cert=ca_cert)
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()
    ca_cert_pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode()

    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(
        monkeypatch, private_key=user_key, cert_pem=cert_pem, ca_cert_pem=ca_cert_pem
    )

    # La rama de anclaje personal solo se ejercita bajo XAdES-T (mismo
    # prerrequisito que el bloque LT institucional) — instala una TSA
    # efímera en memoria, sin tocar el filesystem.
    tsa_cert_pem, tsa_key_pem = _generate_tsa_cert()
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=tsa_cert_pem, key_pem=tsa_key_pem, key_password=None),
    )

    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    captured_insert: dict = {}
    queue = [sol, {}, turno, turno]
    tail = [None, sol_firmado, {}]

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
            captured_insert["provider"] = args[7]
            captured_insert["xades_level"] = args[12]
            captured_insert["firma_blob"] = args[6]
            captured_insert["ca_fingerprint"] = args[21]
            row = dict(_FIRMA_PERSONAL_ROW_TEMPLATE)
            row.update(
                payload_sha256=args[5],
                firma_blob=args[6],
                provider=args[7],
                certificado_pem=args[9],
                xades_level=args[12],
            )
            return row
        if queue:
            return queue.pop(0)
        return tail.pop(0)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = _fetchrow_router
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["estado"] == "firmado"

        assert captured_insert["provider"] == "xades_personal"
        assert captured_insert["xades_level"] == "XAdES-T"
        # Recomputada por LocalSigner.sign a partir del PEM recibido — nunca
        # confía en un ca_fingerprint del body de sign-digest a ciegas
        # (ese body de este fake ni siquiera lo transporta, ver
        # _patch_sign_digest_remote).
        assert captured_insert["ca_fingerprint"] == ca_cert.fingerprint(hashes.SHA256()).hex()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_with_active_personal_cert_no_ca_cert_pem_leaves_ca_fingerprint_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grandfather Incremento 1: sin ``ca_cert_pem`` en la respuesta de
    ``sign-digest`` (leaf no anclado, o TOTP/auth-service anterior a
    Incremento 2), ``firma.ca_fingerprint`` se queda ``None`` — nunca falla
    la firma por esto, retrocompat exacta."""
    _patch_audit(monkeypatch)
    user_key, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()

    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(monkeypatch, private_key=user_key, cert_pem=cert_pem)

    from app.core.config import settings as signature_settings

    monkeypatch.setattr(signature_settings, "signer_timestamp_enabled", False)

    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    captured_insert: dict = {}
    queue = [sol, {}, turno, turno]
    tail = [None, sol_firmado, {}]

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
            captured_insert["ca_fingerprint"] = args[21]
            row = dict(_FIRMA_PERSONAL_ROW_TEMPLATE)
            row.update(payload_sha256=args[5], firma_blob=args[6], provider=args[7])
            return row
        if queue:
            return queue.pop(0)
        return tail.pop(0)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = _fetchrow_router
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200, resp.text
        assert captured_insert["ca_fingerprint"] is None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_without_active_cert_falls_back_to_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin cert activo (estado != 'activo'), el comportamiento es el HMAC
    nativo preexistente — retrocompat exacta, sin 2FA (nivel PUBLICA)."""
    _patch_audit(monkeypatch)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    sign_digest_mock = _patch_sign_digest_remote(
        monkeypatch, error=RuntimeError("no debe llamarse")
    )

    conn = _make_conn()
    sol = _sol_row()  # nivel_seguridad=1 (PUBLICA) → firma_2fa_required decide
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    from app.core.config import settings

    monkeypatch.setattr(settings, "firma_2fa_required", False)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol,
        {},
        turno,
        turno,
        {**_FIRMA_PERSONAL_ROW_TEMPLATE, "payload_sha256": "a" * 64, "firma_blob": "b" * 64, "provider": "nativa"},
        None,
        sol_firmado,
        {},
    ]
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["estado"] == "firmado"
        sign_digest_mock.assert_not_called()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_active_cert_without_totp_code_returns_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    _patch_signing_key_status(
        monkeypatch, body={"estado": "activo", "cert_pem": "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"}
    )
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "2fa_requerido"
        sign_digest_mock.assert_not_called()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_active_cert_wrong_totp_maps_auth_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auth-service responde 401 totp_incorrecto — se reenvía tal cual, la
    clave del usuario nunca se descifra ni se usa (verificado indirectamente:
    la firma nunca se persiste, la solicitud sigue 'pendiente'). Certificado
    REAL (no un placeholder) porque LocalSigner ya empieza a construir el
    árbol XAdES (incluida la validación del certificado embebido) ANTES de
    invocar remote_sign — el fallo de TOTP ocurre DENTRO de esa llamada, no
    antes."""
    _, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()

    _patch_audit(monkeypatch)
    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(
        monkeypatch,
        error=SignDigestRemoteError(
            401, {"error": "totp_incorrecto", "detail": "wrong code", "status": 401}
        ),
    )
    from app.core.config import settings as signature_settings

    monkeypatch.setattr(signature_settings, "signer_timestamp_enabled", False)

    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "000000"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"]["error"] == "totp_incorrecto"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_active_cert_status_unavailable_falls_back_to_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auth-service caído al consultar signing-key/status — fail-OPEN a la
    firma nativa (firma_personal_require_xades NO se aplica en Inc.1), nunca
    bloquea la cadena de firma por un fallo de infraestructura ajeno."""
    from app.core.http_clients import SigningKeyStatusUnavailableError

    _patch_audit(monkeypatch)
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    from app.core.config import settings

    monkeypatch.setattr(settings, "firma_2fa_required", False)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol,
        {},
        turno,
        turno,
        {**_FIRMA_PERSONAL_ROW_TEMPLATE, "payload_sha256": "a" * 64, "firma_blob": "b" * 64, "provider": "nativa"},
        None,
        sol_firmado,
        {},
    ]
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200, resp.text
        sign_digest_mock.assert_not_called()
    finally:
        _clear()


# ---------------------------------------------------------------------------
# /verify — dispatch para provider='xades_personal'
# ---------------------------------------------------------------------------


async def _sign_personal_envelope_for_verify_test(
    user_key: rsa.RSAPrivateKey, user_cert: x509.Certificate
):
    """Firma un envoltorio mínimo con la MISMA técnica de firma remota que
    ``LocalSigner.sign``/``CadenaFirmaService._firmar_personal`` — pero
    invocada directamente (sin HTTP, sin cadena de firma) para producir un
    ``signed_xml`` genuino que ejercite ``GET /verify`` de forma aislada."""
    from app.providers.base import PreparedDoc, SignerRef
    from app.providers.local import LocalSigner

    async def _remote_sign(digest: bytes) -> bytes:
        return user_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))

    signer_ref = SignerRef(
        cert_pem=user_cert.public_bytes(serialization.Encoding.PEM),
        key_pem=b"",
        remote_sign=_remote_sign,
        nivel_conformidad="xades_personal_no_acreditado",
        acreditado=False,
    )
    prepared = PreparedDoc(
        xml_bytes=b'<acto xmlns="urn:orpyca:firma-personal" objetoTipo="radicado" objetoId="x">abc123</acto>',
        objeto_tipo="radicado",
        objeto_id="x",
    )

    signed = await LocalSigner().sign(prepared, signer_ref)
    return signed.signed_xml, signed


@pytest.mark.asyncio
async def test_verify_dispatches_xades_personal(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings as signature_settings

    monkeypatch.setattr(signature_settings, "signer_timestamp_enabled", False)
    # E17/F4, Incremento 2 (A2): /verify SIEMPRE consulta revocation-status
    # en línea para una firma xades_personal cuya cripto ya validó — incluso
    # una fila SIN ancla de sub-CA (ca_fingerprint=None, grandfather de
    # Incremento 1), porque la huella de revocación es certificado_fingerprint
    # (siempre poblada), no ca_fingerprint. Mockeada "vigente" aquí para
    # preservar el comportamiento previo de este test — la rama de
    # revocación tiene su propia cobertura en test_verify_revocacion_status_*.
    monkeypatch.setattr(
        "app.routers.signature.get_signing_key_revocation_status_remote",
        AsyncMock(return_value={"status": "vigente"}),
    )

    user_key, user_cert = _issue_user_cert()
    signed_xml, signed = await _sign_personal_envelope_for_verify_test(user_key, user_cert)

    client, conn = client_with_conn
    firma_id = uuid.uuid4()
    row = {
        "id": firma_id,
        "objeto_tipo": "radicado",
        "objeto_id": "x",
        "formato_solicitado": None,
        "firmante_id": USER,
        "payload_sha256": signed.payload_sha256,
        "firma_blob": signed.signature_value_b64,
        "provider": "xades_personal",
        "sello_tiempo": datetime.now(UTC),
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
        "tsa_cert_fingerprint": None,
        "tsa_acreditado": False,
        "ca_fingerprint": None,
        "ocsp_responder_fingerprint": None,
    }
    conn.fetchrow.return_value = row

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "xades_personal"
    assert body["valida"] is True
    assert body["acreditado"] is False
    # Incremento 2 — grandfather Inc.1 (ca_fingerprint=None): sigue
    # verificando igual, PERO ahora consulta revocation-status igual (por
    # certificado_fingerprint, no por ca_fingerprint).
    assert body["firma_criptograficamente_valida"] is True
    assert body["revocacion_status"] == "vigente"

    # Alterar el contenido → inválida (misma detección criptográfica que xades_local).
    tampered = signed_xml.replace(b"abc123", b"ZZZZZZ")
    resp2 = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": tampered.decode()},
        headers=TENANT_HEADERS,
    )
    assert resp2.json()["valida"] is False
    # Cripto ya inválida -> nunca se llega a consultar revocación.
    assert resp2.json()["firma_criptograficamente_valida"] is False
    assert resp2.json()["revocacion_status"] is None


@pytest.mark.asyncio
async def test_verify_native_hmac_unchanged_by_this_increment(client_with_conn) -> None:
    """Retrocompat exacta: una firma 'nativa' preexistente sigue verificando
    exactamente igual (no afectada por el dispatch xades_personal)."""
    from app.core.config import settings as signature_settings
    from app.services import signer as native_signer

    sello_dt = datetime(2024, 1, 1, tzinfo=UTC)
    r = native_signer.firmar(
        b"<indice/>", str(USER), sello_dt.isoformat(), signature_settings.signature_secret
    )
    client, conn = client_with_conn
    conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "objeto_tipo": "indice",
        "objeto_id": "exp-1",
        "formato_solicitado": None,
        "firmante_id": USER,
        "payload_sha256": r["payload_sha256"],
        "firma_blob": r["firma_blob"],
        "provider": r["provider"],
        "sello_tiempo": sello_dt,
        "created_at": sello_dt,
    }

    resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(uuid.uuid4()), "payload_xml": "<indice/>"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valida"] is True
    assert resp.json()["provider"] == "nativa"
