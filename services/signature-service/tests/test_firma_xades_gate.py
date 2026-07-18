# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests — gate ``firma_personal_require_xades`` (E17/F4, Incremento 3, cierra
Baja #3 del Inc.2). Reutiliza los helpers de ``tests/test_cadena.py``
(fixtures de solicitud/turno), ``tests/test_cadena_2fa.py`` (``_patch_audit``)
y ``tests/test_cadena_personal_signing.py`` (certificado de usuario en
memoria, mocks de ``get_signing_key_status_remote``/``sign_digest_remote``).

Cubre:
  - Gate 'off' — retrocompat byte-idéntica (fail-open a nativa en ambos
    casos de "sin cert", incluido el canal caído).
  - Gate 'clasificados' — nivel 1 conserva el fallback; nivel>=2 sin cert
    o con canal caído DENIEGA (422/503) en vez de degradar, sin consumir
    ningún factor 2FA, con asiento atómico.
  - Gate 'todos' — ninguna firma personal cae a nativa, en ningún nivel.
  - Lote mixto — el gate se re-evalúa POR ÍTEM.
  - Coacción del bool legacy en `Settings` (incluido el fail-fast de un
    valor no reconocido).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import serialization
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.http_clients import SigningKeyStatusUnavailableError
from app.main import app
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import FIRMA_ID, SOL_ID, _exec_sig, _make_conn, _sol_row, _turno_row
from tests.test_cadena_2fa import _patch_audit
from tests.test_cadena_personal_signing import (
    _FIRMA_PERSONAL_ROW_TEMPLATE,
    _clear,
    _issue_user_cert,
    _override,
    _patch_signing_key_status,
    _patch_sign_digest_remote,
)

# Actor with sufficient clearance for CLASIFICADA (nivel_seguridad up to 3).
_CLEARANCE_ROOT = {"is_root": True, "max_level": None}


def _cert_pem() -> str:
    _, user_cert = _issue_user_cert()
    return user_cert.public_bytes(serialization.Encoding.PEM).decode()


async def _post_sign(body: dict | None = None) -> tuple[int, dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/signature/requests/{SOL_ID}/sign",
            json=body or {},
            headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
        )
    return resp.status_code, resp.json()


def _queue_native_fallback(
    conn: AsyncMock, sol: dict, sol_firmado: dict, turno: dict
) -> None:
    """conn.fetchrow queue for a full happy-path nativa signature."""
    conn.fetchrow.side_effect = [
        sol,
        {} if sol["nivel_seguridad"] < 2 else _CLEARANCE_ROOT,
        turno,
        turno,
        {
            **_FIRMA_PERSONAL_ROW_TEMPLATE,
            "payload_sha256": "a" * 64,
            "firma_blob": "b" * 64,
            "provider": "nativa",
        },
        None,
        sol_firmado,
        {} if sol["nivel_seguridad"] < 2 else _CLEARANCE_ROOT,
    ]


def _queue_denial(conn: AsyncMock, sol: dict, turno: dict, *, clearance: dict) -> None:
    """conn.fetchrow queue for a gate denial: no INSERT, no get() tail —
    firmar() raises before reaching either."""
    conn.fetchrow.side_effect = [sol, clearance, turno, turno]


# ---------------------------------------------------------------------------
# Gate 'off' — retrocompat byte-idéntica
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_off_nivel1_sin_cert_falls_back_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    sol_firmado = _sol_row(estado="firmado", nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    _queue_native_fallback(conn, sol, sol_firmado, turno)
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 200, body
        assert body["estado"] == "firmado"
        sign_digest_mock.assert_not_called()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_off_nivel2_sin_cert_native_with_mandatory_2fa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 'off' at nivel_seguridad>=2 still falls back to nativa, but the
    PRE-EXISTING mandatory-2FA rule (nivel>=2, regardless of
    firma_2fa_required) is untouched — 409 2fa_requerido without a
    challenge/totp, never a silent bypass."""
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=2)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, _CLEARANCE_ROOT, turno, turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 409, body
        assert body["detail"]["error"] == "2fa_requerido"
        sign_digest_mock.assert_not_called()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_off_auth_status_unavailable_falls_back_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    sol_firmado = _sol_row(estado="firmado", nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    _queue_native_fallback(conn, sol, sol_firmado, turno)
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 200, body
        sign_digest_mock.assert_not_called()
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Gate 'clasificados'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_clasificados_nivel1_sin_cert_native_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    sol_firmado = _sol_row(estado="firmado", nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    _queue_native_fallback(conn, sol, sol_firmado, turno)
    conn.fetch.return_value = [turno]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 200, body
        assert body["estado"] == "firmado"
        sign_digest_mock.assert_not_called()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_clasificados_nivel1_con_cert_xades_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    monkeypatch.setattr(settings, "signer_timestamp_enabled", False)
    user_key, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()
    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(monkeypatch, private_key=user_key, cert_pem=cert_pem)

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    sol_firmado = _sol_row(estado="firmado", nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    queue = [sol, {}, turno, turno]
    tail = [None, sol_firmado, {}]

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
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
        status_code, body = await _post_sign({"totp_code": "123456"})
        assert status_code == 200, body
        assert body["estado"] == "firmado"
        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 1
        denegada_calls = [
            c
            for c in audit_mock.await_args_list
            if c.kwargs.get("action") == "firma.xades_requerida_denegada"
        ]
        assert len(denegada_calls) == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_clasificados_nivel2_sin_cert_denied_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))
    verify_totp_mock = AsyncMock(side_effect=RuntimeError("no debe llamarse"))
    monkeypatch.setattr("app.services.cadena_service.verify_totp", verify_totp_mock)

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=2)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, sol, turno, clearance=_CLEARANCE_ROOT)
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 422, body
        assert body["detail"]["error"] == "firma_xades_requerida"
        sign_digest_mock.assert_not_called()
        verify_totp_mock.assert_not_called()

        denegada_calls = [
            c
            for c in audit_mock.await_args_list
            if c.kwargs.get("action") == "firma.xades_requerida_denegada"
        ]
        assert len(denegada_calls) == 1
        payload = denegada_calls[0].kwargs["payload"]
        assert payload["solicitud_id"] == str(SOL_ID)
        assert payload["nivel_seguridad"] == 2
        assert payload["gate_modo"] == "clasificados"
        assert payload["motivo"] == "sin_certificado_activo"
        assert payload["factor_consumido"] is False

        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_clasificados_nivel2_con_cert_xades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    monkeypatch.setattr(settings, "signer_timestamp_enabled", False)
    user_key, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()
    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(monkeypatch, private_key=user_key, cert_pem=cert_pem)

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=2)
    sol_firmado = _sol_row(estado="firmado", nivel_seguridad=2)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    queue = [sol, _CLEARANCE_ROOT, turno, turno]
    tail = [None, sol_firmado, _CLEARANCE_ROOT]

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
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
        status_code, body = await _post_sign({"totp_code": "123456"})
        assert status_code == 200, body
        assert body["estado"] == "firmado"
        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_clasificados_nivel2_auth_status_unavailable_denied_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))
    verify_totp_mock = AsyncMock(side_effect=RuntimeError("no debe llamarse"))
    monkeypatch.setattr("app.services.cadena_service.verify_totp", verify_totp_mock)

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, sol, turno, clearance=_CLEARANCE_ROOT)
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 503, body
        assert body["detail"]["error"] == "canal_firma_personal_no_disponible"
        sign_digest_mock.assert_not_called()
        verify_totp_mock.assert_not_called()

        denegada_calls = [
            c
            for c in audit_mock.await_args_list
            if c.kwargs.get("action") == "firma.xades_requerida_denegada"
        ]
        assert len(denegada_calls) == 1
        payload = denegada_calls[0].kwargs["payload"]
        assert payload["nivel_seguridad"] == 3
        assert payload["gate_modo"] == "clasificados"
        assert payload["motivo"] == "canal_status_no_disponible"
        assert payload["factor_consumido"] is False
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Gate 'todos'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_todos_nivel1_sin_cert_denied_422(monkeypatch: pytest.MonkeyPatch) -> None:
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "todos")
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "cert_pem": None})
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, sol, turno, clearance={})
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 422, body
        assert body["detail"]["error"] == "firma_xades_requerida"
        sign_digest_mock.assert_not_called()

        denegada_calls = [
            c
            for c in audit_mock.await_args_list
            if c.kwargs.get("action") == "firma.xades_requerida_denegada"
        ]
        assert len(denegada_calls) == 1
        assert denegada_calls[0].kwargs["payload"]["gate_modo"] == "todos"
        assert denegada_calls[0].kwargs["payload"]["motivo"] == "sin_certificado_activo"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_todos_nivel1_con_cert_xades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "todos")
    monkeypatch.setattr(settings, "signer_timestamp_enabled", False)
    user_key, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()
    _patch_signing_key_status(monkeypatch, body={"estado": "activo", "cert_pem": cert_pem})
    _patch_sign_digest_remote(monkeypatch, private_key=user_key, cert_pem=cert_pem)

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    sol_firmado = _sol_row(estado="firmado", nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    queue = [sol, {}, turno, turno]
    tail = [None, sol_firmado, {}]

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
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
        status_code, body = await _post_sign({"totp_code": "123456"})
        assert status_code == 200, body
        assert body["estado"] == "firmado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_gate_todos_nivel1_auth_caido_denied_503(monkeypatch: pytest.MonkeyPatch) -> None:
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "todos")
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    sign_digest_mock = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))

    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=1)
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    conn.fetchval.side_effect = [True, 1]
    _queue_denial(conn, sol, turno, clearance={})
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        status_code, body = await _post_sign()
        assert status_code == 503, body
        assert body["detail"]["error"] == "canal_firma_personal_no_disponible"
        sign_digest_mock.assert_not_called()

        denegada_calls = [
            c
            for c in audit_mock.await_args_list
            if c.kwargs.get("action") == "firma.xades_requerida_denegada"
        ]
        assert len(denegada_calls) == 1
        assert denegada_calls[0].kwargs["payload"]["motivo"] == "canal_status_no_disponible"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Lote mixto — el gate se re-evalúa POR ÍTEM (sin reutilización de cert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_lote_gate_denials_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tres ítems bajo 'clasificados': A (nivel1, con cert) -> firmado;
    B (nivel2, sin cert) -> omitido/firma_xades_requerida; C (nivel3, canal
    caído) -> omitido/canal_firma_personal_no_disponible. El cert de A NUNCA
    cubre a B/C — cada llamada a get_signing_key_status_remote resuelve de
    forma independiente."""
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "clasificados")
    monkeypatch.setattr(settings, "signer_timestamp_enabled", False)

    sid_a, sid_b, sid_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user_key, user_cert = _issue_user_cert()
    cert_pem = user_cert.public_bytes(serialization.Encoding.PEM).decode()

    call_count = {"n": 0}

    async def _status_side_effect(authorization):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"estado": "activo", "cert_pem": cert_pem}
        if call_count["n"] == 2:
            return {"estado": "no_configurado", "cert_pem": None}
        raise SigningKeyStatusUnavailableError("auth-service inalcanzable")

    monkeypatch.setattr(
        "app.services.cadena_service.get_signing_key_status_remote",
        AsyncMock(side_effect=_status_side_effect),
    )
    _patch_sign_digest_remote(monkeypatch, private_key=user_key, cert_pem=cert_pem)

    conn = _make_conn()
    sol_a = _sol_row(nivel_seguridad=1)
    sol_a_firmado = _sol_row(estado="firmado", nivel_seguridad=1)
    turno_a = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    sol_b = _sol_row(nivel_seguridad=2)
    turno_b = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    sol_c = _sol_row(nivel_seguridad=3)
    turno_c = _turno_row(orden=1, firmante_id=USER, estado="pendiente")

    pre_a = [sol_a, {}, turno_a, turno_a]
    post_a = [None, sol_a_firmado, {}]
    pre_b = [sol_b, _CLEARANCE_ROOT, turno_b, turno_b]
    pre_c = [sol_c, _CLEARANCE_ROOT, turno_c, turno_c]
    order = iter(pre_a + post_a + pre_b + pre_c)

    async def _fetchrow_router(query, *args, **kwargs):
        if "INSERT INTO firma" in query:
            row = dict(_FIRMA_PERSONAL_ROW_TEMPLATE)
            row.update(payload_sha256=args[5], firma_blob=args[6], provider=args[7])
            return row
        return next(order)

    # check_permission (once, whole batch request) + es_firmante x3 items.
    conn.fetchval.side_effect = [True, 1, 1, 1]
    conn.fetchrow.side_effect = _fetchrow_router
    conn.fetch.return_value = [turno_a]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/batch",
                json={
                    "items": [
                        {"solicitud_id": str(sid_a), "totp_code": "123456"},
                        {"solicitud_id": str(sid_b)},
                        {"solicitud_id": str(sid_c)},
                    ]
                },
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200, resp.text
        resultados = resp.json()["resultados"]
        assert len(resultados) == 3
        assert resultados[0]["resultado"] == "firmado"
        assert resultados[1]["resultado"] == "omitido"
        assert resultados[1]["detalle"] == "firma_xades_requerida"
        assert resultados[2]["resultado"] == "omitido"
        assert resultados[2]["detalle"] == "canal_firma_personal_no_disponible"

        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 1
        denegada_calls = [
            c
            for c in audit_mock.await_args_list
            if c.kwargs.get("action") == "firma.xades_requerida_denegada"
        ]
        assert len(denegada_calls) == 2
        motivos = {c.kwargs["payload"]["motivo"] for c in denegada_calls}
        assert motivos == {"sin_certificado_activo", "canal_status_no_disponible"}
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Coacción del env legacy (Settings)
# ---------------------------------------------------------------------------


def test_env_coercion_bool_false_maps_to_off() -> None:
    for value in (False, "false", "0", "no", ""):
        s = Settings(firma_personal_require_xades=value)
        assert s.firma_personal_require_xades == "off", value


def test_env_coercion_bool_true_maps_to_todos() -> None:
    for value in (True, "true", "1", "yes"):
        s = Settings(firma_personal_require_xades=value)
        assert s.firma_personal_require_xades == "todos", value


def test_env_coercion_literals_pass_through() -> None:
    for value in ("off", "clasificados", "todos", "CLASIFICADOS", " todos "):
        s = Settings(firma_personal_require_xades=value)
        assert s.firma_personal_require_xades == value.strip().lower()


def test_env_coercion_unrecognized_value_raises() -> None:
    with pytest.raises(ValidationError):
        Settings(firma_personal_require_xades="garbage")
