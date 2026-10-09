# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Smoke tests — 2FA por OTP-correo en la firma personal (E06 Inc.2, RF-FIR-13).

Same style as tests/test_cadena.py (mocked asyncpg.Connection, no real DB).
Two techniques keep these tests deterministic, network-free and sleep-free:

1. Outbound HTTP is never exercised — app.services.cadena_service imports
   get_signer_identity/send_otp_email by name, so they are monkeypatched directly
   on the module (AsyncMock) instead of touching httpx/auth-service/notification-service.
2. app.services.cadena_service._audit.append is monkeypatched to a no-op
   AsyncMock in every test here — it decouples these tests from
   orpycamcp_common.audit's internal fetchval/execute call count (its own
   prev_hash lookup + advisory lock + insert), which is an implementation
   detail of a different module. Tests that care about audit *payload*
   content instead assert on the captured call kwargs.

A verifiable OTP round-trip needs no cryptographic mocking either: the test
builds otp_hash with the SAME app.core.otp.hmac_otp(otp, settings.signature_secret)
that the service uses, and reads it back as a plain asyncpg.Record-shaped dict.
Time-based checks (expira_at, cooldown) are asserted by fabricating
creado_at/expira_at values in the past/future directly in the mock row — no
sleep() is ever needed.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_tenant_conn
from app.core.http_clients import OtpEmailDeliveryError, SignerEmailError, SignerIdentity
from app.core.otp import hmac_otp
from app.main import app
from app.services import cadena_service
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import FIRMANTE2, SOL_ID, _exec_sig, _make_conn, _turno_row

CHALLENGE_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
FIRMA_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
NOW = datetime.now(tz=UTC)
DOC_HASH = "a" * 64


def _sol_row(
    estado: str = "pendiente",
    nivel_seguridad: int = 1,
    payload_sha256: str | None = DOC_HASH,
) -> dict:
    return {
        "id": SOL_ID,
        "objeto_tipo": "radicado",
        "objeto_id": "2024-DEMO-E-000001",
        "titulo": "Oficio de prueba",
        "payload_sha256": payload_sha256,
        "estado": estado,
        "creado_por": USER,
        "nivel_seguridad": nivel_seguridad,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _challenge_row(
    *,
    otp: str = "123456",
    firmante_id: uuid.UUID = USER,
    solicitud_id: uuid.UUID = SOL_ID,
    hash_documento: str = DOC_HASH,
    estado: str = "pendiente",
    intentos: int = 0,
    max_intentos: int = 5,
    expira_at: datetime | None = None,
    creado_at: datetime | None = None,
) -> dict:
    return {
        "challenge_id": CHALLENGE_ID,
        "solicitud_id": solicitud_id,
        "firmante_id": firmante_id,
        "hash_documento": hash_documento,
        "factor_tipo": "otp_email",
        "otp_hash": hmac_otp(
            otp, settings.signature_secret, tenant_slug="demo", challenge_id=CHALLENGE_ID
        ),
        "key_version": 1,
        "estado": estado,
        "intentos": intentos,
        "max_intentos": max_intentos,
        "expira_at": expira_at or (NOW + timedelta(minutes=5)),
        "verificado_at": None,
        "consumido_at": None,
        "creado_at": creado_at or NOW,
        "canal_masked": "j***@example.com",
        "tenant_slug": "demo",
    }


def _override(conn: AsyncMock) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep


def _clear() -> None:
    app.dependency_overrides.clear()


def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """No-ops _audit.append and returns the mock so tests can assert on payload."""
    mock_append = AsyncMock(return_value="fakehash")
    monkeypatch.setattr("app.services.cadena_service._audit.append", mock_append)
    return mock_append


def _patch_email(
    monkeypatch: pytest.MonkeyPatch,
    *,
    email: str = "jdoe@example.com",
    user_id: uuid.UUID = USER,
    get_email_error: Exception | None = None,
    send_email_error: Exception | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    mock_get = (
        AsyncMock(side_effect=get_email_error)
        if get_email_error
        else AsyncMock(return_value=SignerIdentity(email=email, user_id=str(user_id)))
    )
    mock_send = AsyncMock(side_effect=send_email_error) if send_email_error else AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.cadena_service.get_signer_identity", mock_get)
    monkeypatch.setattr("app.services.cadena_service.send_otp_email", mock_send)
    return mock_get, mock_send


# ---------------------------------------------------------------------------
# POST /requests/{id}/challenge — emisión
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_challenge_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row()

    conn.fetchval.side_effect = [True, 1]  # check_permission only (audit is mocked)  # check_permission | es_firmante
    conn.fetchrow.side_effect = [
        sol,            # get_solicitud_for_update
        {},             # resolve_caller_clearance
        turno,          # turno_activo_for_update
        None,           # get_activo — no live challenge
        challenge_row,  # crear() RETURNING
    ]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["challenge_id"] == str(CHALLENGE_ID)
        assert body["factor_tipo"] == "otp_email"
        assert body["canal"] == "j***@example.com"
        assert set(body.keys()) == {"challenge_id", "factor_tipo", "canal", "expira_at"}
        mock_send.assert_awaited_once()
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_challenge_not_found_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_audit(monkeypatch)
    _patch_email(monkeypatch)
    conn = _make_conn()
    conn.fetchval.side_effect = [True]
    conn.fetchrow.side_effect = [None]  # get_solicitud_for_update → not found

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_no_encontrada"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_challenge_wrong_turn_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_audit(monkeypatch)
    _patch_email(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    turno_other = _turno_row(firmante_id=FIRMANTE2)

    # `fetchval`: 1) clearance del llamante, 2) es_firmante → SÍ pertenece a la
    # cadena. Esa segunda comprobación es la que distingue este caso —«estás en
    # la cadena, pero el turno es de otro»— del de quien no pertenece.
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno_other]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_su_turno"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_challenge_a_quien_no_esta_en_la_cadena_no_le_dice_que_espere_turno() -> None:
    """Pedir el código de firma sin pertenecer a la cadena.

    El guard respondía «no es el turno de este actor», y a quien no está en la
    cadena eso le afirma algo falso: no espera turno, es que nunca le va a
    llegar. Se queda esperando un aviso que no existe — el mismo daño que un
    filtro silencioso.

    El colapso tampoco protegía nada: `POST /sign` sobre esa misma solicitud ya
    distingue los dos casos y responde `no_es_firmante`. Un colapso que solo
    existe en uno de los dos caminos no es una defensa, es una incoherencia.
    """
    conn = _make_conn()
    sol = _sol_row()

    # 1) clearance OK, 2) es_firmante → None: el actor NO está en la cadena.
    conn.fetchval.side_effect = [True, None]
    conn.fetchrow.side_effect = [sol, {}]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_firmante"
        assert "no pertenece" in resp.json()["detail"]["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_challenge_sin_huella_se_ata_al_identificador(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una cadena sobre `radicado`/`expediente` SÍ se puede firmar.

    Este test afirmaba lo contrario: exigía `422 payload_no_fijado`, y con eso
    codificaba el defecto. `CADENA_PAYLOAD_OBLIGATORIO` deja esos dos tipos
    fuera a propósito —se firman como conjunto—, así que el contrato permite
    crear la cadena sin huella; y `emitir_desafio` la reclamaba igual. Las dos
    reglas nunca se encontraron y el resultado era una cadena **imposible de
    firmar**: la pantalla ofrecía «Firmar» y el paso 1 moría nombrando una
    columna de la base.

    Ahora el desafío se ata al SHA-256 del identificador —exactamente los
    bytes que `firmar` sella cuando no hay huella de contenido—, así que no
    promete más de lo que la firma cumple. La aserción fuerte es sobre el
    `hash_documento` PERSISTIDO: comprobar solo el `201` pasaría también si la
    huella se guardara en blanco o distinta de la que usa la verificación.
    """
    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    conn = _make_conn()
    sol = _sol_row(payload_sha256=None)
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row()

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [sol, {}, turno, None, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 201
        mock_send.assert_awaited_once()

        esperado = hashlib.sha256(
            f"{sol['objeto_tipo']}:{sol['objeto_id']}".encode()
        ).hexdigest()
        insert = [
            c for c in conn.fetchrow.await_args_list if c.args and "firma_challenge" in c.args[0]
        ]
        assert insert, "no se ejecutó el INSERT del desafío"
        assert esperado in insert[-1].args, (
            "el desafío no quedó atado al digest del identificador: "
            "la verificación lo rechazaría como «de otro documento»"
        )
    finally:
        _clear()


def test_huella_para_desafio_coincide_en_emision_y_verificacion() -> None:
    """Emisión y verificación tienen que calcular lo MISMO.

    Son dos puntos distintos del código y antes cada uno leía el campo por su
    cuenta. Si divergieran, todo desafío de un tipo sin huella se rechazaría
    como «de otro documento» — un fallo que solo aparecería al teclear el OTP.
    Por eso hay una sola función, y por eso se comprueba que es determinista y
    que respeta la huella de contenido cuando la hay.
    """
    sin_huella = _sol_row(payload_sha256=None)
    assert cadena_service.huella_para_desafio(sin_huella) == hashlib.sha256(
        b"radicado:2024-DEMO-E-000001"
    ).hexdigest()
    assert cadena_service.huella_para_desafio(sin_huella) == cadena_service.huella_para_desafio(
        _sol_row(payload_sha256=None)
    )
    assert cadena_service.huella_para_desafio(_sol_row()) == DOC_HASH


@pytest.mark.asyncio
async def test_create_challenge_cooldown_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_audit(monkeypatch)
    _patch_email(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    # Issued 5s ago — well inside the 30s default cooldown, no sleep() needed.
    activo = _challenge_row(creado_at=NOW - timedelta(seconds=5))

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [sol, {}, turno, activo]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 429
        assert resp.json()["detail"]["error"] == "challenge_cooldown"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_challenge_replaces_expired_cooldown_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live challenge issued MORE than the cooldown ago is expired-and-replaced."""
    _patch_audit(monkeypatch)
    _patch_email(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    stale_active = _challenge_row(
        creado_at=NOW - timedelta(seconds=settings.otp_reenvio_cooldown_seconds + 1)
    )
    new_challenge = _challenge_row()

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [sol, {}, turno, stale_active, new_challenge]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 201
        # The stale challenge's UPDATE ... SET estado = 'expirado' must have run.
        expirar_calls = [
            c for c in conn.execute.call_args_list if "estado = 'expirado'" in str(c.args[0])
        ]
        assert len(expirar_calls) == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_challenge_email_unavailable_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAIL-CLOSED (RF-FIR-13): auth-service /me failure aborts the challenge."""
    _patch_audit(monkeypatch)
    _patch_email(monkeypatch, get_email_error=SignerEmailError("auth-service unreachable"))
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [sol, {}, turno, None]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "canal_2fa_no_disponible"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_challenge_notification_unavailable_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAIL-CLOSED (RF-FIR-13): notification-service /send failure aborts the
    challenge too — even though the row was already INSERTed inside the tx."""
    _patch_audit(monkeypatch)
    _patch_email(
        monkeypatch,
        send_email_error=OtpEmailDeliveryError("notification-service unreachable"),
    )
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row()

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [sol, {}, turno, None, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "canal_2fa_no_disponible"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /requests/{id}/sign — gate 2FA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_missing_challenge_returns_409_2fa_requerido() -> None:
    """settings.firma_2fa_required defaults True — no monkeypatch needed here."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [sol, {}, turno, turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "2fa_requerido"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_challenge_invalido_returns_409() -> None:
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, None]  # last: get_for_update → None

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(uuid.uuid4()), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "challenge_invalido"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_challenge_ajeno_returns_403() -> None:
    """Challenge belongs to a different firmante than the caller."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row(firmante_id=FIRMANTE2)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "challenge_ajeno"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_challenge_documento_distinto_returns_409() -> None:
    conn = _make_conn()
    sol = _sol_row(payload_sha256="a" * 64)
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row(hash_documento="b" * 64)  # different document

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "challenge_documento_distinto"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_challenge_expirado_marks_state_and_returns_409() -> None:
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row(expira_at=NOW - timedelta(seconds=1))  # already expired

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "challenge_expirado"
        # _gate_2fa uses the parameterized marcar_estado($1, $2) — not the
        # literal-'expirado' expirar() used by the emitir_challenge replacement path.
        marcar_calls = [
            c
            for c in conn.execute.call_args_list
            if "SET estado = $2 WHERE challenge_id" in str(c.args[0])
        ]
        assert len(marcar_calls) == 1
        assert marcar_calls[0].args[1:] == (CHALLENGE_ID, "expirado")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_challenge_consumido_replay_returns_409() -> None:
    """Anti-replay: a challenge already marked 'consumido' cannot be reused."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row(estado="consumido")

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "challenge_consumido"
        # Replay must not consume/mutate the challenge again nor sign anything.
        consumir_calls = [
            c for c in conn.execute.call_args_list if "estado = 'consumido'" in str(c.args[0])
        ]
        assert len(consumir_calls) == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_challenge_ya_bloqueado_returns_409() -> None:
    """A challenge already in 'bloqueado' state (from a prior request) is rejected
    up front, without re-touching the attempt counter."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row(estado="bloqueado", intentos=5, max_intentos=5)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "challenge_bloqueado"
        intentos_calls = [
            c for c in conn.execute.call_args_list if "intentos = $2" in str(c.args[0])
        ]
        assert len(intentos_calls) == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_otp_incorrecto_returns_401_and_persists_intentos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    RF-FIR-13 durability: the failed-attempt counter increment must NOT be lost
    even though the overall sign attempt is aborted (409/401, no firma row).
    """
    audit_mock = _patch_audit(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    # otp_hash is for "123456"; the caller sends a wrong code.
    challenge_row = _challenge_row(otp="123456", intentos=0, max_intentos=5)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "000000"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"] == "otp_incorrecto"
        assert body["detail"]["intentos_restantes"] == 4

        # Durable persist: UPDATE firma_challenge SET intentos=1 ran (not rolled back —
        # this test's mock cannot assert COMMIT-vs-ROLLBACK directly, but it does prove
        # the code path never raises *inside* the `async with self._conn.transaction()`
        # block for this branch, which is what makes the commit happen; see
        # CadenaFirmaService._gate_2fa docstring).
        intentos_calls = [
            c for c in conn.execute.call_args_list if "intentos = $2" in str(c.args[0])
        ]
        assert len(intentos_calls) == 1
        assert intentos_calls[0].args[2:] == (1, "pendiente")

        # No firma row was created and no turn was marked (the sign was skipped).
        marcar_turno_calls = [
            c for c in conn.execute.call_args_list if "UPDATE firma_cadena" in str(c.args[0])
        ]
        assert len(marcar_turno_calls) == 0

        # firma.challenge.fallido was audited (never the OTP itself).
        fallido_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.challenge.fallido"
        ]
        assert len(fallido_calls) == 1
        payload = fallido_calls[0].kwargs["payload"]
        assert payload["intentos"] == 1
        assert payload["intentos_restantes"] == 4
        assert "otp" not in payload and "otp_hash" not in payload
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_otp_incorrecto_last_attempt_blocks_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_audit(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    challenge_row = _challenge_row(otp="123456", intentos=4, max_intentos=5)

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "000000"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 401
        assert resp.json()["detail"]["intentos_restantes"] == 0
        intentos_calls = [
            c for c in conn.execute.call_args_list if "intentos = $2" in str(c.args[0])
        ]
        assert intentos_calls[0].args[2:] == (5, "bloqueado")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_with_valid_otp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full happy path: correct OTP consumes the challenge and produces the firma."""
    audit_mock = _patch_audit(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)
    challenge_row = _challenge_row(otp="123456")
    firma_ins = {
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

    conn.fetchval.side_effect = [True, 1]  # check_permission | es_firmante
    conn.fetchrow.side_effect = [
        sol,          # get_or_404
        {},           # resolve_caller_clearance (_assert_can_read)
        turno,        # turno_activo (early)
        turno,        # turno_activo_for_update (txn)
        challenge_row,  # _gate_2fa: get_for_update
        firma_ins,    # FirmaRepository.insert
        None,         # next_active → None → last signer
        sol_firmado,  # get() get_solicitud
        {},           # get()._assert_can_read
    ]
    conn.fetch.return_value = [turno_firmado]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "firmado"

        # The challenge was consumed (terminal, anti-replay).
        consumir_calls = [
            c for c in conn.execute.call_args_list if "estado = 'consumido'" in str(c.args[0])
        ]
        assert len(consumir_calls) == 1

        # firma.turno_firmado was enriched with the 2FA outcome (never the OTP).
        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 1
        payload = firmado_calls[0].kwargs["payload"]
        assert payload["factor_tipo"] == "otp_email"
        assert payload["challenge_id"] == str(CHALLENGE_ID)
        assert payload["resultado_2fa"] == "verificado"
        # El asiento lleva el SHA-256 de los BYTES FIRMADOS y el instante de
        # consumo (RF-FIR-13/14, auto-contención del asiento). El firmante calcula
        # el HMAC sobre los bytes del propio `sol["payload_sha256"]`, así que ese
        # valor es el SHA-256 de la cadena hexadecimal — el hash del hash, NO el
        # hash del documento.
        #
        # Este test ya lo sabía y lo describía con exactitud, y aun así la clave se
        # llamaba `hash_documento`. El comportamiento era conocido y el nombre
        # seguía afirmando otra cosa: quien leyera `audit_log` entendía «hash del
        # documento» sobre un valor que no lo es. Se corrigió el nombre, no el
        # valor — el HMAC de las firmas ya emitidas cubre esos bytes.
        assert payload["payload_firmado_sha256"] == hashlib.sha256(DOC_HASH.encode()).hexdigest()
        assert "hash_documento" not in payload, (
            "el asiento del turno firmado no puede volver a publicar el hash del "
            "payload firmado bajo un nombre que promete el del documento"
        )
        assert payload["consumido_at"] is not None
        assert "otp" not in payload and "otp_hash" not in payload
        # Auditoría 2026-10-08: el asiento de la firma consumada dice bajo qué
        # política XAdES se firmó y sobre qué huella de documento.
        assert payload["gate_modo"] == settings.firma_personal_require_xades
        assert payload["huella_documento_sha256"] == DOC_HASH
        assert payload["huella_documento_sha256"] != payload["payload_firmado_sha256"]
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /batch — per-item OTP (Q2): one own challenge each, no cross-reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_sign_per_item_otp_one_success_one_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two items, each with its OWN challenge_id/otp (Q2): sid1's correct OTP
    signs and consumes its challenge; sid2's wrong OTP is 'omitido' with
    'otp_incorrecto' and does not abort or affect sid1."""
    audit_mock = _patch_audit(monkeypatch)
    conn = _make_conn()
    sid1, sid2 = uuid.uuid4(), uuid.uuid4()
    challenge2_id = uuid.uuid4()

    sol1 = _sol_row()
    sol1_firmado = _sol_row(estado="firmado")
    turno1 = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno1_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)
    challenge1 = _challenge_row(otp="123456", solicitud_id=sid1)

    sol2 = _sol_row()
    turno2 = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    # Own challenge, own OTP ("654321") — the caller will send the wrong code.
    challenge2 = _challenge_row(otp="654321", solicitud_id=sid2)

    firma_ins = {
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

    conn.fetchval.side_effect = [True, 1, 1]  # check_permission | es_firmante_sid1 | es_firmante_sid2
    conn.fetchrow.side_effect = [
        sol1, {}, turno1, turno1,       # sid1: get_or_404 | clearance | turno_activo x2
        challenge1,                     # sid1: _gate_2fa get_for_update — correct OTP
        firma_ins,                      # sid1: FirmaRepository.insert
        None,                           # sid1: next_active → None
        sol1_firmado, {},               # sid1: get() get_solicitud + clearance
        sol2, {}, turno2, turno2,       # sid2: get_or_404 | clearance | turno_activo x2
        challenge2,                     # sid2: _gate_2fa get_for_update — wrong OTP
    ]
    conn.fetch.return_value = [turno1_firmado]  # only sid1 reaches list_turnos (via get())
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/batch",
                json={
                    "items": [
                        {"solicitud_id": str(sid1), "challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                        {"solicitud_id": str(sid2), "challenge_id": str(challenge2_id), "otp": "000000"},
                    ]
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        resultados = resp.json()["resultados"]
        assert len(resultados) == 2
        assert resultados[0]["resultado"] == "firmado"
        assert resultados[1]["resultado"] == "omitido"
        assert resultados[1]["detalle"] == "otp_incorrecto"

        # sid1's challenge was consumed; sid2's failed attempt was persisted —
        # both are durable, independent state changes (no cross-item reuse, Q2).
        consumir_calls = [
            c for c in conn.execute.call_args_list if "estado = 'consumido'" in str(c.args[0])
        ]
        assert len(consumir_calls) == 1
        intentos_calls = [
            c for c in conn.execute.call_args_list if "intentos = $2" in str(c.args[0])
        ]
        assert len(intentos_calls) == 1

        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        assert len(firmado_calls) == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_2fa_disabled_ignores_challenge_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.firma_2fa_required=False → legacy happy path, gate never runs."""
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    audit_mock = _patch_audit(monkeypatch)
    conn = _make_conn()
    sol = _sol_row()
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)
    firma_ins = {
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

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol, {}, turno, turno,
        firma_ins,   # no _gate_2fa call at all — straight to FirmaRepository.insert
        None,
        sol_firmado,
        {},
    ]
    conn.fetch.return_value = [turno_firmado]
    conn.execute.side_effect = _exec_sig

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        firmado_calls = [
            c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
        ]
        payload = firmado_calls[0].kwargs["payload"]
        assert payload["factor_tipo"] is None
        assert payload["challenge_id"] is None
        assert payload["resultado_2fa"] is None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_challenge_acto_distinto_returns_409() -> None:
    """A challenge issued for a DIFFERENT solicitud_id (same firmante, same
    hash_documento — e.g. the same file attached to two radicados) must not
    be consumable here, even though its firmante_id and hash_documento both
    match (Media finding — cross-solicitud OTP reuse)."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row(firmante_id=USER)
    otro_sol_id = uuid.uuid4()
    challenge_row = _challenge_row(solicitud_id=otro_sol_id)  # issued for a different act

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno, turno, challenge_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": "123456"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "challenge_acto_distinto"
        # Must not consume the challenge nor sign anything.
        consumir_calls = [
            c for c in conn.execute.call_args_list if "estado = 'consumido'" in str(c.args[0])
        ]
        assert len(consumir_calls) == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_forces_2fa_for_clasificada_even_if_globally_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nivel_seguridad >= 2 (RESERVADA/CLASIFICADA) must not be able to skip
    2FA via firma_2fa_required=False (Media finding — non-bypassable gate for
    classified acts, DUR 1074/2015 Cap. 47)."""
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)
    turno = _turno_row(firmante_id=USER)
    clearance_row = {"is_root": True, "max_level": None}  # actor cleared for CLASIFICADA

    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, clearance_row, turno, turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "2fa_requerido"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_challenge_solicitud_no_pendiente_returns_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chain that is no longer 'pendiente' (already 'rechazado'/'firmado')
    must reject a challenge request outright — rechazar() only resolves the
    active turn, leaving later turns 'pendiente', so turno_activo_for_update
    would otherwise still hand out a real OTP for a dead chain (Baja finding)."""
    _patch_audit(monkeypatch)
    _patch_email(monkeypatch)
    conn = _make_conn()
    sol = _sol_row(estado="rechazado")

    # check_permission, es_firmante (ahora ANTES que el estado, AREA-04-09 Media-2)
    conn.fetchval.side_effect = [True, True]
    conn.fetchrow.side_effect = [sol, {}]  # get_solicitud_for_update, resolve_caller_clearance

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers={**TENANT_HEADERS, "Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "solicitud_no_pendiente"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Schema — LoteItem / FirmarRequest OTP format
# ---------------------------------------------------------------------------


def test_firmar_request_rejects_non_6_digit_otp() -> None:
    from pydantic import ValidationError

    from app.schemas.cadena import FirmarRequest

    with pytest.raises(ValidationError):
        FirmarRequest(challenge_id=uuid.uuid4(), otp="12a456")
    with pytest.raises(ValidationError):
        FirmarRequest(challenge_id=uuid.uuid4(), otp="12345")


def test_lote_request_requires_items_with_challenge_and_otp() -> None:
    from pydantic import ValidationError

    from app.schemas.cadena import LoteRequest

    with pytest.raises(ValidationError):
        LoteRequest(items=[])

    lote = LoteRequest(
        items=[{"solicitud_id": uuid.uuid4(), "challenge_id": uuid.uuid4(), "otp": "123456"}]
    )
    assert lote.items[0].otp == "123456"


def test_otp_validator_tracks_configured_otp_longitud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baja/Media finding: the OTP regex must be derived from settings.otp_longitud,
    not a hardcoded 6 — otherwise a deployment configuring a different length
    would reject every OTP it itself emits (config-trap)."""
    from pydantic import ValidationError

    from app.schemas.cadena import FirmarRequest

    monkeypatch.setattr(settings, "otp_longitud", 8)
    # An 8-digit code is now accepted...
    FirmarRequest(challenge_id=uuid.uuid4(), otp="12345678")
    # ...and the old fixed-6 length is rejected.
    with pytest.raises(ValidationError):
        FirmarRequest(challenge_id=uuid.uuid4(), otp="123456")
