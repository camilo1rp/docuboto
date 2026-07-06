# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Smoke tests — 2FA por TOTP (RFC 6238) en la firma personal, elevando el
OTP-correo del Inc.2 (E06). Reutiliza los helpers de tests/test_cadena.py y
tests/test_cadena_2fa.py — mismo estilo (asyncpg.Connection mockeado, sin BD
real; ``verify_totp`` monkeypatched en vez de tocar httpx/auth-service).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_tenant_conn
from app.core.http_clients import TotpVerifyUnavailableError
from app.main import app
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import SOL_ID, _exec_sig, _make_conn, _turno_row
from tests.test_cadena_2fa import FIRMA_ID, NOW, _patch_audit, _sol_row


def _override(conn: AsyncMock) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep


def _clear() -> None:
    app.dependency_overrides.clear()


def _patch_verify_totp(monkeypatch: pytest.MonkeyPatch, *, result: dict | None = None, error: Exception | None = None) -> AsyncMock:
    mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    monkeypatch.setattr("app.services.cadena_service.verify_totp", mock)
    return mock


_FIRMA_INS = {
    "id": FIRMA_ID,
    "objeto_tipo": "radicado",
    "objeto_id": "2024-DEMO-E-000001",
    "formato_solicitado": None,
    "firmante_id": USER,
    "payload_sha256": "a" * 64,
    "firma_blob": "b" * 64,
    "provider": "nativa",
    "sello_tiempo": NOW,
    "created_at": NOW,
}


# ---------------------------------------------------------------------------
# POST /requests/{id}/sign — TOTP happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_with_valid_totp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    audit_mock = _patch_audit(monkeypatch)
    _patch_verify_totp(monkeypatch, result={"valido": True, "timestep": 12345, "factor_tipo": "totp"})
    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [
        sol,            # get_or_404
        {},             # resolve_caller_clearance (_assert_can_read)
        turno,          # turno_activo (early)
        turno,          # turno_activo_for_update (txn)
        _FIRMA_INS,     # FirmaRepository.insert — NO challenge fetchrow for TOTP
        None,           # next_active → None
        sol_firmado,    # get() get_solicitud
        {},             # get()._assert_can_read
    ]
    conn.fetch.return_value = [turno_firmado]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "firmado"

        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 1
        payload = firmado_calls[0].kwargs["payload"]
        assert payload["factor_tipo"] == "totp"
        # Never the literal string "None" — must be a real JSON null.
        assert payload["challenge_id"] is None
        assert payload["resultado_2fa"] == "verificado"
        assert payload["consumido_at"] is not None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_totp_not_active_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """totp_code present but auth-service reports no active credential —
    accionable 422, NEVER a silent fallback to the OTP-correo channel."""
    _patch_audit(monkeypatch)
    _patch_verify_totp(monkeypatch, result={"valido": False, "motivo": "no_activo"})
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "totp_no_activo"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_totp_incorrect_returns_401_with_intentos_restantes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_mock = _patch_audit(monkeypatch)
    _patch_verify_totp(
        monkeypatch,
        result={"valido": False, "motivo": "codigo_incorrecto", "intentos_restantes": 3},
    )
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "000000"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"] == "totp_incorrecto"
        assert body["detail"]["intentos_restantes"] == 3

        fallido_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.challenge.fallido"
        ]
        assert len(fallido_calls) == 1
        payload = fallido_calls[0].kwargs["payload"]
        assert payload["factor_tipo"] == "totp"
        assert payload["challenge_id"] is None
        assert payload["motivo"] == "codigo_incorrecto"
        assert "totp_code" not in payload  # the code itself is never audited
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_totp_locked_returns_423(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_audit(monkeypatch)
    _patch_verify_totp(
        monkeypatch,
        result={"valido": False, "motivo": "bloqueado", "locked_until": "2026-01-01T00:00:00Z"},
    )
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "000000"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 423
        assert resp.json()["detail"]["error"] == "totp_bloqueado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_totp_auth_unavailable_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAIL-CLOSED: auth-service unreachable during TOTP verify aborts the sign."""
    _patch_audit(monkeypatch)
    _patch_verify_totp(monkeypatch, error=TotpVerifyUnavailableError("auth-service unreachable"))
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "canal_2fa_no_disponible"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_totp_preferred_over_otp_email_when_both_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both totp_code AND challenge_id/otp present -> TOTP wins, the OTP-correo
    channel (firma_challenge) is never even queried."""
    audit_mock = _patch_audit(monkeypatch)
    verify_mock = _patch_verify_totp(
        monkeypatch, result={"valido": True, "timestep": 999, "factor_tipo": "totp"}
    )
    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol, {}, turno, turno,
        _FIRMA_INS,  # straight to signing — no firma_challenge fetchrow at all
        None,
        sol_firmado, {},
    ]
    conn.fetch.return_value = [turno_firmado]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={
                    "totp_code": "123456",
                    "challenge_id": str(uuid.uuid4()),
                    "otp": "654321",
                },
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200
        verify_mock.assert_awaited_once()
        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert firmado_calls[0].kwargs["payload"]["factor_tipo"] == "totp"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_totp_disabled_falls_back_to_2fa_requerido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """settings.totp_enabled=False -> totp_code is ignored entirely (as if
    absent); with no OTP-correo fields either, the usual 409 applies."""
    monkeypatch.setattr(settings, "totp_enabled", False)
    verify_mock = _patch_verify_totp(monkeypatch, result={"valido": True})
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "2fa_requerido"
        verify_mock.assert_not_awaited()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_totp_satisfies_mandatory_gate_for_clasificada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nivel_seguridad >= 2 forces the 2FA gate even with firma_2fa_required
    disabled — TOTP is a valid satisfactor there too (never a bypass)."""
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    audit_mock = _patch_audit(monkeypatch)
    _patch_verify_totp(monkeypatch, result={"valido": True, "timestep": 1, "factor_tipo": "totp"})
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)
    sol_firmado = _sol_row(nivel_seguridad=3, estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)
    clearance_row = {"is_root": True, "max_level": None}

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol, clearance_row, turno, turno,
        _FIRMA_INS, None, sol_firmado, clearance_row,
    ]
    conn.fetch.return_value = [turno_firmado]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"totp_code": "123456"},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200
        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert firmado_calls[0].kwargs["payload"]["factor_tipo"] == "totp"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /batch — per-item totp_code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_sign_with_totp_code_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    audit_mock = _patch_audit(monkeypatch)
    _patch_verify_totp(monkeypatch, result={"valido": True, "timestep": 1, "factor_tipo": "totp"})
    conn = _make_conn()
    sid = uuid.uuid4()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [
        sol, {}, turno, turno,
        _FIRMA_INS, None, sol_firmado, {},
    ]
    conn.fetch.return_value = [turno_firmado]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/batch",
                json={"items": [{"solicitud_id": str(sid), "totp_code": "123456"}]},
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200
        resultados = resp.json()["resultados"]
        assert resultados[0]["resultado"] == "firmado"
        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert firmado_calls[0].kwargs["payload"]["factor_tipo"] == "totp"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Schema — FirmarRequest/LoteItem totp_code format
# ---------------------------------------------------------------------------


def test_firmar_request_rejects_non_6_digit_totp_code() -> None:
    from pydantic import ValidationError

    from app.schemas.cadena import FirmarRequest

    with pytest.raises(ValidationError):
        FirmarRequest(totp_code="12a456")
    with pytest.raises(ValidationError):
        FirmarRequest(totp_code="12345")
    FirmarRequest(totp_code="123456")  # accepted


def test_lote_item_allows_totp_code_without_otp_email_fields() -> None:
    from app.schemas.cadena import LoteItem

    item = LoteItem(solicitud_id=uuid.uuid4(), totp_code="123456")
    assert item.challenge_id is None
    assert item.otp is None
    assert item.totp_code == "123456"
