# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
T8 — el segundo factor y la credencial son del MISMO usuario al que se atribuye
la firma, y el correo no baja el factor de quien ya tiene TOTP.

Cubre AREA-04-05 (identidad bearer vs actor), AREA-04-02 (OTP-correo vetado con
TOTP activo en nivel >= 2), AREA-04-12 (fallback a nativa con el canal caído deja
asiento) y AREA-04-06 (el estado de la credencial se resuelve antes del lock).

Misma técnica que ``test_cadena_2fa.py``: conexión simulada con colas de
``fetchrow`` y colaboradores HTTP sustituidos por nombre. Los dobles de auth-service
tienen la FORMA REAL de su contrato (``user_id`` en el estado de TOTP, en el de la
credencial y en el veredicto de verificación): su prueba vive en auth-service.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.http_clients import (
    SigningKeyStatusUnavailableError,
    TotpStatusUnavailableError,
)
from app.main import app
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import SOL_ID, _exec_sig, _make_conn, _turno_row
from tests.test_cadena_2fa import (
    CHALLENGE_ID,
    FIRMA_ID,
    _challenge_row,
    _clear,
    _override,
    _patch_audit,
    _patch_email,
    _sol_row,
)
from tests.test_cadena_personal_signing import (
    _patch_sign_digest_remote,
    _patch_signing_key_status,
)
from tests.test_cadena_totp import _FIRMA_INS, _patch_verify_totp

AUTH = {"Authorization": "Bearer faketoken"}
AJENO = uuid.UUID("99999999-9999-9999-9999-999999999999")
CERT_PEM = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"


def _acciones(audit_mock: AsyncMock) -> list[str]:
    return [c.kwargs["action"] for c in audit_mock.await_args_list]


async def _post(path: str, body: dict | None = None) -> tuple[int, dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/signature/requests/{SOL_ID}/{path}",
            json=body if body is not None else {},
            headers={**TENANT_HEADERS, **AUTH},
        )
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# AREA-04-05 — identidad del bearer vs actor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_no_sale_si_bearer_y_actor_divergen(monkeypatch: pytest.MonkeyPatch) -> None:
    """/me dice que el bearer es de OTRA persona: 403, sin desafío y sin correo."""
    audit_mock = _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch, user_id=AJENO)
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [
        _sol_row(),                       # get_solicitud (previa a la transacción)
        {},                               # resolve_caller_clearance
        _turno_row(firmante_id=USER),     # turno_activo (sin bloqueo)
        _sol_row(),                       # get_solicitud_for_update
        _turno_row(firmante_id=USER),     # turno_activo_for_update
        None,                             # get_activo
        # (crear() sería la 5.ª: no debe ejecutarse)
    ]
    _override(conn)
    try:
        status_code, body = await _post("challenge")
    finally:
        _clear()
    assert status_code == 403, body
    assert body["detail"]["error"] == "identidad_incoherente"
    mock_send.assert_not_awaited()
    assert conn.fetchrow.await_count == 6  # ni INSERT del desafío
    assert "firma.identidad_incoherente" in _acciones(audit_mock)
    assert "firma.challenge.emitido" not in _acciones(audit_mock)
    emitido = next(
        c for c in audit_mock.await_args_list
        if c.kwargs["action"] == "firma.identidad_incoherente"
    )
    assert emitido.kwargs["actor"] == str(USER)
    assert emitido.kwargs["payload"]["via"] == "otp_email"


@pytest.mark.asyncio
async def test_otp_si_bearer_y_actor_coinciden_el_correo_sale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control positivo de la anterior: con la misma persona, el desafío se emite."""
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch, user_id=USER)
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(), {}, _turno_row(firmante_id=USER), _sol_row(), _turno_row(firmante_id=USER),
        None, _challenge_row(),
    ]
    _override(conn)
    try:
        status_code, body = await _post("challenge")
    finally:
        _clear()
    assert status_code == 201, body
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_totp_verificado_de_otro_usuario_no_firma(monkeypatch: pytest.MonkeyPatch) -> None:
    """El TOTP es válido, pero del dueño del bearer: no se marca el turno."""
    audit_mock = _patch_audit(monkeypatch)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    _patch_verify_totp(
        monkeypatch,
        result={"valido": True, "timestep": 7, "factor_tipo": "totp", "user_id": str(AJENO)},
    )
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(), {}, _turno_row(firmante_id=USER), _turno_row(firmante_id=USER),
        # (el INSERT de la firma sería la 5.ª: no debe ejecutarse)
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post("sign", {"totp_code": "123456"})
    finally:
        _clear()
    assert status_code == 403, body
    assert body["detail"]["error"] == "identidad_incoherente"
    assert conn.fetchrow.await_count == 4
    assert "firma.turno_firmado" not in _acciones(audit_mock)
    # El asiento se escribe en la transacción que cierra sin levantar: sobrevive.
    assert "firma.identidad_incoherente" in _acciones(audit_mock)
    assert not any("UPDATE firma_cadena" in str(c.args[0]) for c in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_totp_sin_user_id_en_la_respuesta_tampoco_firma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un auth-service que no dice de quién era el factor no vale: fail-closed."""
    _patch_audit(monkeypatch)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    _patch_verify_totp(monkeypatch, result={"valido": True, "timestep": 7, "factor_tipo": "totp"})
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(), {}, _turno_row(firmante_id=USER), _turno_row(firmante_id=USER),
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post("sign", {"totp_code": "123456"})
    finally:
        _clear()
    assert status_code == 403, body
    assert body["detail"]["error"] == "identidad_incoherente"


@pytest.mark.asyncio
async def test_credencial_personal_de_otro_usuario_no_firma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La clave personal es la del bearer; si no es la del actor, no se firma ni
    se llega a pedir el factor al canal remoto."""
    audit_mock = _patch_audit(monkeypatch)
    _patch_signing_key_status(
        monkeypatch, body={"estado": "activo", "user_id": str(AJENO), "cert_pem": CERT_PEM}
    )
    sign_digest = _patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(), {}, _turno_row(firmante_id=USER), _turno_row(firmante_id=USER),
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post("sign", {"totp_code": "123456"})
    finally:
        _clear()
    assert status_code == 403, body
    assert body["detail"]["error"] == "identidad_incoherente"
    sign_digest.assert_not_called()
    assert "firma.identidad_incoherente" in _acciones(audit_mock)


@pytest.mark.asyncio
async def test_sign_personal_credencial_de_otro_usuario_no_firma(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests import test_sign_personal as tsp

    audit_mock = tsp._patch_audit(monkeypatch)
    upload = tsp._patch_upload(monkeypatch)
    tsp._patch_signing_key_status(
        monkeypatch, body={"estado": "activo", "user_id": str(AJENO), "cert_pem": CERT_PEM}
    )
    sign_digest = tsp._patch_sign_digest_remote(monkeypatch, error=RuntimeError("no debe llamarse"))
    client, conn = client_with_conn
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None
    resp = await client.post(
        "/api/v1/signature/sign-personal",
        json={
            "payload_xml": tsp._PAYLOAD_XML,
            "objeto_tipo": "acta_transferencia_personal",
            "objeto_id": str(uuid.uuid4()),
            "totp_code": "123456",
        },
        headers={**TENANT_HEADERS, **AUTH},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "identidad_incoherente"
    sign_digest.assert_not_called()
    upload.assert_not_called()
    assert "firma.identidad_incoherente" in _acciones(audit_mock)


@pytest.mark.asyncio
async def test_get_signer_identity_manda_el_tenant_y_devuelve_el_user_id() -> None:
    """Sin ``X-Tenant-Slug``, /me no resuelve ``auth_users.id`` y devuelve el sub de
    Keycloak: la comparación con el actor fallaría siempre."""
    from unittest.mock import MagicMock, patch

    from app.core import http_clients

    resp = MagicMock(status_code=200, text="")
    resp.json.return_value = {"email": "a@b.co", "user_id": str(USER)}
    inner = AsyncMock()
    inner.get = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("app.core.http_clients.httpx.AsyncClient", return_value=cm):
        ident = await http_clients.get_signer_identity("Bearer x", "demo")
    assert ident.user_id == str(USER) and ident.email == "a@b.co"
    headers = inner.get.call_args.kwargs["headers"]
    assert headers["X-Tenant-Slug"] == "demo"
    assert headers["Authorization"] == "Bearer x"


# ---------------------------------------------------------------------------
# AREA-04-02 — el correo no baja el factor de quien ya tiene TOTP (nivel >= 2)
# ---------------------------------------------------------------------------


def _estado_totp(monkeypatch, *, activo: bool = True, user_id=USER, error=None) -> AsyncMock:
    mock = (
        AsyncMock(side_effect=error)
        if error
        else AsyncMock(return_value={"activo": activo, "user_id": str(user_id)})
    )
    monkeypatch.setattr("app.services.cadena_service.get_totp_status_remote", mock)
    return mock


@pytest.mark.asyncio
async def test_nivel2_con_totp_activo_sin_cert_rechaza_otp_correo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firmar con challenge_id+otp válidos y sin totp_code, con TOTP activo: 409
    ``totp_requerido`` y el desafío NO se consume."""
    _patch_audit(monkeypatch)
    _estado_totp(monkeypatch, activo=True)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    # Cola de un firmar que NO llega a leer el desafío: si lo leyera, la fila
    # (get_for_update) sería la 5.ª y esta cola no la tiene.
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=2),
        {"is_root": True, "max_level": None},
        _turno_row(firmante_id=USER),
        _turno_row(firmante_id=USER),
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post(
            "sign", {"challenge_id": str(CHALLENGE_ID), "otp": "123456"}
        )
    finally:
        _clear()
    assert status_code == 409, body
    assert body["detail"]["error"] == "totp_requerido"
    assert conn.fetchrow.await_count == 4  # el desafío ni se leyó
    assert not any("firma_challenge" in str(c.args[0]) for c in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_nivel2_con_totp_activo_emitir_desafio_correo_es_totp_requerido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    _estado_totp(monkeypatch, activo=True)
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=2),
        {"is_root": True, "max_level": None},
        _turno_row(firmante_id=USER),
        _sol_row(nivel_seguridad=2),
        _turno_row(firmante_id=USER),
        None,
    ]
    _override(conn)
    try:
        status_code, body = await _post("challenge")
    finally:
        _clear()
    assert status_code == 409, body
    assert body["detail"]["error"] == "totp_requerido"
    mock_send.assert_not_awaited()
    assert conn.fetchrow.await_count == 6  # no se creó el desafío


@pytest.mark.asyncio
async def test_nivel2_estado_totp_no_consultable_no_baja_al_correo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: si no se puede saber, no se acepta el correo (503, no fallback)."""
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    _estado_totp(monkeypatch, error=TotpStatusUnavailableError("auth-service caído"))
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=2),
        {"is_root": True, "max_level": None},
        _turno_row(firmante_id=USER),
        _sol_row(nivel_seguridad=2),
        _turno_row(firmante_id=USER),
        None,
    ]
    _override(conn)
    try:
        status_code, body = await _post("challenge")
    finally:
        _clear()
    assert status_code == 503, body
    assert body["detail"]["error"] == "canal_2fa_no_disponible"
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_nivel1_con_totp_activo_el_correo_sigue_firmando(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: el veto es solo de nivel >= 2; ni siquiera se consulta a auth."""
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    estado = _estado_totp(monkeypatch, activo=True)
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=1), {}, _turno_row(firmante_id=USER),
        _sol_row(nivel_seguridad=1), _turno_row(firmante_id=USER), None, _challenge_row(),
    ]
    _override(conn)
    try:
        status_code, body = await _post("challenge")
    finally:
        _clear()
    assert status_code == 201, body
    estado.assert_not_awaited()
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_nivel2_sin_totp_el_correo_firma(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: TOTP no enrolado -> el correo vale (el factor más débil de los DOS
    ENROLADOS es el correo)."""
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    _estado_totp(monkeypatch, activo=False)
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=2),
        {"is_root": True, "max_level": None},
        _turno_row(firmante_id=USER),
        _sol_row(nivel_seguridad=2),
        _turno_row(firmante_id=USER),
        None,
        _challenge_row(),
    ]
    _override(conn)
    try:
        status_code, body = await _post("challenge")
    finally:
        _clear()
    assert status_code == 201, body
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_estado_totp_de_otro_bearer_no_levanta_el_veto_ni_pasa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El estado de TOTP del bearer ajeno no dice nada del actor: sin esto, bastaría
    un bearer sin TOTP para saltarse el del actor."""
    audit_mock = _patch_audit(monkeypatch)
    _estado_totp(monkeypatch, activo=False, user_id=AJENO)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(nivel_seguridad=2),
        {"is_root": True, "max_level": None},
        _turno_row(firmante_id=USER),
        _turno_row(firmante_id=USER),
    ]
    conn.execute.side_effect = _exec_sig
    _override(conn)
    try:
        status_code, body = await _post(
            "sign", {"challenge_id": str(CHALLENGE_ID), "otp": "123456"}
        )
    finally:
        _clear()
    assert status_code == 403, body
    assert body["detail"]["error"] == "identidad_incoherente"
    assert "firma.identidad_incoherente" in _acciones(audit_mock)


# ---------------------------------------------------------------------------
# AREA-04-12 — el fallback a nativa con el canal caído deja asiento
# ---------------------------------------------------------------------------


def _cola_nativa_nivel1(conn: AsyncMock) -> None:
    sol = _sol_row(nivel_seguridad=1)  # huella del productor (AREA-04-09 Media-2)
    turno = _turno_row(firmante_id=USER)
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol, {}, turno, turno,
        {**_FIRMA_INS, "id": FIRMA_ID, "provider": "nativa"},
        None,
        {**sol, "estado": "firmado"},
        {},
    ]
    conn.fetch.return_value = [{**turno, "estado": "firmado", "firma_id": FIRMA_ID}]
    conn.execute.side_effect = _exec_sig


@pytest.mark.asyncio
async def test_canal_signing_key_caido_no_degrada_en_silencio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(
        monkeypatch, error=SigningKeyStatusUnavailableError("auth-service inalcanzable")
    )
    conn = _make_conn()
    _cola_nativa_nivel1(conn)
    _override(conn)
    try:
        status_code, body = await _post("sign")
    finally:
        _clear()
    assert status_code == 200, body
    acciones = _acciones(audit_mock)
    assert "firma.fallback_nativa_canal_caido" in acciones
    # Mismo bloque transaccional que la firma: el asiento va ANTES del
    # `firma.turno_firmado` que cierra el acto, no después ni aparte.
    assert acciones.index("firma.fallback_nativa_canal_caido") < acciones.index(
        "firma.turno_firmado"
    )
    fb = next(
        c for c in audit_mock.await_args_list
        if c.kwargs["action"] == "firma.fallback_nativa_canal_caido"
    )
    assert fb.kwargs["payload"]["firma_id"] == str(FIRMA_ID)
    assert fb.kwargs["payload"]["motivo"] == "canal_status_no_disponible"


@pytest.mark.asyncio
async def test_sin_certificado_no_es_canal_caido_y_no_deja_asiento_de_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: «sin certificado» (auth respondió) NO es una degradación a avisar."""
    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    _patch_signing_key_status(monkeypatch, body={"estado": "no_configurado", "user_id": str(USER)})
    conn = _make_conn()
    _cola_nativa_nivel1(conn)
    _override(conn)
    try:
        status_code, body = await _post("sign")
    finally:
        _clear()
    assert status_code == 200, body
    assert "firma.fallback_nativa_canal_caido" not in _acciones(audit_mock)


# ---------------------------------------------------------------------------
# AREA-04-06 — el estado de la credencial se resuelve ANTES del row-lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_personal_se_resuelve_antes_del_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_signing_key_status_remote`` (HTTP a auth-service) no puede ocurrir con
    el row-lock del turno tomado: va antes de ``turno_activo_for_update``."""
    _patch_audit(monkeypatch)
    monkeypatch.setattr(settings, "firma_personal_require_xades", "off")
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    orden: list[str] = []

    async def _status(authorization):
        orden.append("status_http")
        return {"estado": "no_configurado", "user_id": str(USER)}

    monkeypatch.setattr("app.services.cadena_service.get_signing_key_status_remote", _status)

    conn = _make_conn()
    _cola_nativa_nivel1(conn)
    original = conn.fetchrow.side_effect
    consumidas = iter(original)

    async def _fetchrow(query, *args, **kwargs):
        if "FOR UPDATE" in query and "firma_cadena" in query:
            orden.append("lock_turno")
        return next(consumidas)

    conn.fetchrow.side_effect = _fetchrow
    _override(conn)
    try:
        status_code, body = await _post("sign")
    finally:
        _clear()
    assert status_code == 200, body
    assert "status_http" in orden and "lock_turno" in orden
    assert orden.index("status_http") < orden.index("lock_turno")
