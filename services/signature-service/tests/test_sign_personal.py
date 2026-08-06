# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests — ``POST /api/v1/signature/sign-personal`` (E17/F4, Incremento 1):
firma PERSONAL PKI por-usuario de un ``payload_xml`` ARBITRARIO, endpoint
reutilizable (primer consumidor: el acta de entrega de transferencia,
archive-service).

Reutiliza la generación de certificados de test de
``tests/test_cadena_personal_signing.py`` (misma técnica de firma remota) —
sin duplicar la construcción de una CA/leaf en memoria.
"""

from __future__ import annotations

import uuid
from base64 import b64encode
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from app.core.http_clients import SignDigestRemoteError, SigningKeyStatusUnavailableError
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena_personal_signing import _issue_user_cert

_PAYLOAD_XML = '<acta xmlns="urn:orpycamcp:test:acta"><campo>valor</campo></acta>'


def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_append = AsyncMock(return_value="fakehash")
    monkeypatch.setattr("app.services.personal_signing._audit.append", mock_append)
    return mock_append


def _patch_upload(monkeypatch: pytest.MonkeyPatch, *, key: str = "file-id/acta-firmado.xml") -> AsyncMock:
    mock = AsyncMock(return_value=key)
    monkeypatch.setattr("app.services.personal_signing.storage_client.upload_signed_xml", mock)
    return mock


def _patch_signing_key_status(
    monkeypatch: pytest.MonkeyPatch, *, body: dict | None = None, error: Exception | None = None
) -> AsyncMock:
    mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=body)
    monkeypatch.setattr("app.services.personal_signing.get_signing_key_status_remote", mock)
    return mock


def _patch_sign_digest_remote(
    monkeypatch: pytest.MonkeyPatch,
    *,
    private_key=None,
    cert_pem: str | None = None,
    error: Exception | None = None,
) -> AsyncMock:
    if error is not None:
        mock = AsyncMock(side_effect=error)
    else:
        assert private_key is not None

        async def _fake_sign(authorization, digest, totp_code):
            signature = private_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))
            return {
                "signature_value_b64": b64encode(signature).decode("ascii"),
                "certificado_pem": cert_pem,
                "certificado_fingerprint": "deadbeef",
                "serial": "ab12",
                "not_after": None,
                "ca_cert_pem": None,
            }

        mock = AsyncMock(side_effect=_fake_sign)
    monkeypatch.setattr("app.services.personal_signing.sign_digest_remote", mock)
    return mock


def _disable_tsa(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings as signature_settings

    monkeypatch.setattr(signature_settings, "signer_timestamp_enabled", False)


@pytest.mark.asyncio
async def test_sign_personal_ok_produces_xades_personal_and_uploads(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_tsa(monkeypatch)
    audit_mock = _patch_audit(monkeypatch)
    upload_mock = _patch_upload(monkeypatch)

    user_key, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()
    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(monkeypatch, private_key=user_key, cert_pem=cert_pem)

    client, conn = client_with_conn
    transferencia_id = uuid.uuid4()

    captured_insert: dict = {}

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
            captured_insert["provider"] = args[7]
            captured_insert["minio_key_firmado"] = args[10]
            captured_insert["tipo_firma"] = args[13]
            row = {
                "id": args[0],
                "objeto_tipo": args[1],
                "objeto_id": args[2],
                "formato_solicitado": args[3],
                "firmante_id": args[4],
                "payload_sha256": args[5],
                "firma_blob": args[6],
                "provider": args[7],
                "sello_tiempo": args[8],
                "created_at": args[8],
                "certificado_pem": args[9],
                "minio_key_firmado": args[10],
                "nivel_conformidad": args[11],
                "xades_level": args[12],
                "tipo_firma": args[13],
                "certificado_fingerprint": args[14],
                "tsa_timestamp": args[17],
                "tsa_acreditado": args[19],
            }
            return row
        # resolve_caller_clearance's auth_users lookup: default None -> PUBLICA.
        return None

    conn.fetchval.return_value = True  # PERM_FIRMA granted
    conn.fetchrow.side_effect = _fetchrow_router

    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": _PAYLOAD_XML,
            "objeto_tipo": "acta_transferencia_personal",
            "objeto_id": str(transferencia_id),
            "totp_code": "123456",
            "firmante_id": str(USER),
        },
        headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["provider"] == "xades_personal"
    assert body["tipo_firma"] == "personal"
    assert body["acreditado"] is False
    assert body["minio_key_firmado"] == "file-id/acta-firmado.xml"

    assert captured_insert["provider"] == "xades_personal"
    assert captured_insert["tipo_firma"] == "personal"

    upload_mock.assert_awaited_once()

    firma_calls = [
        c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.creada"
    ]
    assert len(firma_calls) == 1
    assert firma_calls[0].kwargs["payload"]["tipo_firma"] == "personal"


@pytest.mark.asyncio
async def test_sign_personal_invalid_totp_rejected_without_persisting(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_tsa(monkeypatch)
    _patch_audit(monkeypatch)
    upload_mock = _patch_upload(monkeypatch)

    _, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()
    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(
        monkeypatch,
        error=SignDigestRemoteError(
            401, {"error": "totp_incorrecto", "detail": "wrong code", "status": 401}
        ),
    )

    client, conn = client_with_conn
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": _PAYLOAD_XML,
            "objeto_tipo": "acta_transferencia_personal",
            "objeto_id": str(uuid.uuid4()),
            "totp_code": "000000",
        },
        headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"] == "totp_incorrecto"
    upload_mock.assert_not_called()


@pytest.mark.asyncio
async def test_sign_personal_firmante_mismatch_returns_403(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No se mockea get_signing_key_status_remote/sign_digest_remote: el
    # mismatch se detecta ANTES de tocar el canal remoto.
    status_mock = _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": "x"})

    client, conn = client_with_conn
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None

    otro_usuario = uuid.uuid4()
    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": _PAYLOAD_XML,
            "objeto_tipo": "acta_transferencia_personal",
            "objeto_id": str(uuid.uuid4()),
            "totp_code": "123456",
            "firmante_id": str(otro_usuario),
        },
        headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "firmante_no_coincide"
    status_mock.assert_not_called()


@pytest.mark.asyncio
async def test_sign_personal_no_active_cert_returns_422(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    client, conn = client_with_conn
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": _PAYLOAD_XML,
            "objeto_tipo": "acta_transferencia_personal",
            "objeto_id": str(uuid.uuid4()),
            "totp_code": "123456",
        },
        headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "sin_certificado_activo"
    sign_digest_mock.assert_not_called()


@pytest.mark.asyncio
async def test_sign_personal_auth_channel_down_returns_503(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )

    client, conn = client_with_conn
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": _PAYLOAD_XML,
            "objeto_tipo": "acta_transferencia_personal",
            "objeto_id": str(uuid.uuid4()),
            "totp_code": "123456",
        },
        headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error"] == "canal_firma_personal_no_disponible"


@pytest.mark.asyncio
async def test_sign_personal_clearance_gate_no_read_up(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``objeto_tipo="radicado"`` con nivel CLASIFICADA (3) y el llamante sin
    clearance (PUBLICA, fetchrow None -> resolve_caller_clearance default)
    -> 404 neutro, sin llegar a tocar el canal de firma personal."""
    status_mock = _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": "x"})

    client, conn = client_with_conn
    conn.fetchval.side_effect = [True, 3]  # PERM_FIRMA ok, resolver_nivel_por_objeto -> 3
    conn.fetchrow.return_value = None  # resolve_caller_clearance -> PUBLICA (1)

    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": _PAYLOAD_XML,
            "objeto_tipo": "radicado",
            "objeto_id": "2024-DEMO-E-000001",
            "totp_code": "123456",
        },
        headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "object_not_found"
    status_mock.assert_not_called()


@pytest.mark.asyncio
async def test_sign_personal_gate_no_depende_del_objeto_tipo_declarado(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El gate no-read-up de `/sign-personal` colgaba de `objeto_tipo == "radicado"`.

    `SignPersonalRequest.objeto_tipo` es texto libre (tiene que serlo: por aquí pasan
    las actas institucionales), así que declarar cualquier otro valor sobre el
    identificador de un radicado CLASIFICADA evaluaba el gate contra nivel 1 y lo
    saltaba. Ahora la resolución no mira el tipo: manda el nivel real del objeto.
    """
    status_mock = _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": "x"})

    client, conn = client_with_conn
    conn.fetchval.side_effect = [True, 3]  # PERM_FIRMA ok, nivel real del objeto = 3
    conn.fetchrow.return_value = None  # resolve_caller_clearance -> PUBLICA (1)

    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": _PAYLOAD_XML,
            "objeto_tipo": "acta_transferencia_personal",  # tipo que NO es "radicado"
            "objeto_id": "2024-DEMO-E-000001",
            "totp_code": "123456",
        },
        headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "object_not_found"
    status_mock.assert_not_called()
