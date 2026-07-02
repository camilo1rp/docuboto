# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cobertura XAdES-B enveloped real de /sign-xml y /verify (E06, ADR-016, Inc.1).

Complementa ``tests/test_signature.py`` (que cubre el flujo HMAC 'nativa'
preexistente) con el camino nuevo del Incremento 1: ``objeto_tipo="indice"``
con ``settings.signer_provider="xades_local"`` (el default) produce una firma
XAdES-B REAL vía ``app.providers.local.LocalSigner``.

No se toca el filesystem ni se depende de ``scripts/gen-dev-signing-cert.sh``:
cada test genera su propio par de claves RSA-2048 efímero (``cryptography``) y
parchea ``app.routers.signature.load_seal`` directamente — el mismo patrón que
documenta la memoria del proyecto para pruebas de este proveedor. El upload a
storage-service también se mockea (``app.clients.storage_client.upload_signed_xml``)
para no requerir un storage-service real en la suite unitaria.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.clients import storage_client
from app.core.keys import SealNotFoundError, cert_fingerprint
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
        if q.startswith("INSERT INTO firma"):
            (
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
            ) = args
            row = {
                "id": uuid.uuid4(),
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
            }
            self.rows[str(row["id"])] = row
            return row
        if q.startswith("SELECT") and "FROM firma WHERE id" in q:
            (firma_id,) = args
            return self.rows.get(str(firma_id))
        raise AssertionError(f"Unexpected fetchrow query in _FakeFirmaTable: {query!r}")


@pytest_asyncio.fixture
async def signature_env(client_with_conn, monkeypatch):
    """(client, conn, table, uploads, install_seal) listos para probar xades_local.

    ``install_seal(ref_or_dict)`` acepta un `SignerRef` único (usado para
    cualquier tenant) o un `dict[str, SignerRef]` para simular sellos
    distintos por tenant (aislamiento, test 11). Un slug sin entrada en el
    dict levanta `SealNotFoundError`, igual que `app.core.keys.load_seal` con
    el filesystem real cuando no hay sello ni fallback `_dev/`.
    """
    client, conn = client_with_conn
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
            monkeypatch.setattr("app.routers.signature.load_seal", lambda slug: seal)
        else:

            def _dispatch(slug: str) -> SignerRef:
                if slug not in seal:
                    raise SealNotFoundError(f"no seal montado para tenant {slug!r}")
                return seal[slug]

            monkeypatch.setattr("app.routers.signature.load_seal", _dispatch)

    return client, conn, table, uploads, _install_seal


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
# 1) Round-trip XAdES-B: sign-xml -> verify=true, sin sobre-declarar (7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_round_trip(signature_env) -> None:
    client, conn, table, uploads, install_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    body = resp.json()

    assert body["provider"] == "xades_local"
    assert body["xades_level"] == "XAdES-B"
    assert body["tipo_firma"] == "sello_sistema"
    assert body["nivel_conformidad"] == "xades_b_no_acreditado"
    # RF-FIR-15 — nunca sobre-declarar conformidad (7).
    assert body["acreditado"] is False
    assert body["certificado_fingerprint"] == cert_fingerprint(cert_pem)
    assert len(body["certificado_fingerprint"]) == 64
    assert body["minio_key_firmado"] == uploads[0]["object_key"]
    # firmante_id atribuido por X-User-Id (RF-FIR-11), no por el sello institucional.
    assert body["firmante_id"] == str(USER)

    firma_id = body["firma_id"]
    signed_xml = uploads[0]["content"]
    assert b"<ds:Signature" in signed_xml or b":Signature" in signed_xml

    verify_resp = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": firma_id, "payload_xml": signed_xml.decode()},
        headers=TENANT_HEADERS,
    )
    assert verify_resp.status_code == 200
    vbody = verify_resp.json()
    assert vbody["valida"] is True
    assert vbody["provider"] == "xades_local"
    assert vbody["xades_level"] == "XAdES-B"
    assert vbody["acreditado"] is False
    assert vbody["motivo"] is None


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
    client, conn, table, uploads, install_seal = signature_env
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
    client, conn, table, uploads, install_seal = signature_env
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
    client, conn, table, uploads, install_seal = signature_env
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
    client, conn, table, uploads, install_seal = signature_env
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
    client, conn, table, uploads, install_seal = signature_env
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
    client, conn, table, uploads, install_seal = signature_env
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
    client, conn, table, uploads, install_seal = signature_env
    now = datetime.now(UTC)
    cert_pem, key_pem = _generate_cert(
        "orpycamcp-dev-expired",
        not_before=now - timedelta(days=400),
        not_after=now - timedelta(days=30),
    )
    install_seal(_signer_ref(cert_pem, key_pem))

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
    assert vbody["motivo"] == "certificado_fuera_de_vigencia"


# ---------------------------------------------------------------------------
# 5) Atribución: firmante_id == X-User-Id + audit 'firma.creada'; sin header -> 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_xml_xades_attribution_and_audit(signature_env) -> None:
    client, conn, table, uploads, install_seal = signature_env
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
    assert payload["xades_level"] == "XAdES-B"
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
    client, conn, table, uploads, install_seal = signature_env
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
    client, conn, table, uploads, install_seal = signature_env
    install_seal({})  # ningún tenant tiene sello

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "signing_seal_unavailable"


@pytest.mark.asyncio
async def test_sign_xml_xades_storage_upload_failure_returns_503(signature_env, monkeypatch) -> None:
    """storage-service no acepta la subida -> 503 signed_index_upload_failed,
    la fila `firma` no llega a persistirse (fetchrow del INSERT nunca se llama)."""
    client, conn, table, uploads, install_seal = signature_env
    cert_pem, key_pem = _generate_cert("orpycamcp-dev")
    install_seal(_signer_ref(cert_pem, key_pem))

    async def _failing_upload(*, tenant_slug, filename, content, uploaded_by=None):
        raise storage_client.StorageUploadError("storage-service inalcanzable: boom")

    monkeypatch.setattr(storage_client, "upload_signed_xml", _failing_upload)

    resp = await _sign_indice(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "signed_index_upload_failed"
    assert table.rows == {}
