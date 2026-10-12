# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
T8 — huecos de cableado que las suites con dobles por nombre no ven.

1. Contrato SALIENTE hacia auth-service, medido en el cable con
   ``httpx.MockTransport`` (el ``conftest`` sustituye ``get_totp_status_remote``
   por nombre en TODA la suite, y los demás tests sustituyen
   ``get_signer_identity``: ninguno comprueba que las cabeceras salgan).
2. ``key_version`` de la fila ``firma_challenge`` y de la fila ``firma`` en el
   camino de la CADENA: ``verify_otp`` debe usar la versión de la fila y el
   INSERT de ``firma`` debe llevar la versión con que se firmó (un literal en
   cualquiera de los dos pasaba todas las suites anteriores).
"""

from __future__ import annotations

import re
import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core import http_clients
from app.core.config import settings
from app.core.http_clients import (
    SignerEmailError,
    TotpStatusUnavailableError,
    get_signer_identity,
    get_totp_status_remote,
)
from app.core.otp import hmac_otp
from app.core.secret_derivation import KEY_VERSION_ACTUAL, KEY_VERSION_LEGACY
from app.main import app
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import SOL_ID, _exec_sig, _make_conn, _turno_row
from tests.test_cadena_2fa import (
    CHALLENGE_ID,
    FIRMA_ID,
    NOW,
    _challenge_row,
    _clear,
    _override,
    _patch_audit,
    _patch_email,
    _sol_row,
)

# ---------------------------------------------------------------------------
# 1. Contrato saliente en el cable
# ---------------------------------------------------------------------------


def _wire(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Redirige TODO ``httpx.AsyncClient`` de http_clients a un MockTransport."""
    vistas: list[httpx.Request] = []

    def _h(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return handler(request)

    real = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_h)
        return real(*args, **kwargs)

    monkeypatch.setattr(http_clients.httpx, "AsyncClient", _factory)
    return vistas


@pytest.mark.asyncio
async def test_get_signer_identity_envia_bearer_y_tenant_y_devuelve_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vistas = _wire(
        monkeypatch,
        lambda r: httpx.Response(200, json={"email": "a@b.co", "user_id": str(USER)}),
    )
    ident = await get_signer_identity("Bearer tok", "demo")
    assert ident.email == "a@b.co" and ident.user_id == str(USER)
    (req,) = vistas
    assert req.method == "GET" and req.url.path == "/api/v1/auth/me"
    assert req.headers["Authorization"] == "Bearer tok"
    # Sin X-Tenant-Slug /me devuelve el `sub` de Keycloak y la comparación con el
    # actor falla SIEMPRE (o, peor, nunca llega a hacerse).
    assert req.headers["X-Tenant-Slug"] == "demo"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"email": "a@b.co"}, {"user_id": str(USER)}, {}])
async def test_get_signer_identity_sin_email_o_sin_user_id_es_fail_closed(
    monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(200, json=body))
    with pytest.raises(SignerEmailError):
        await get_signer_identity("Bearer tok", "demo")


@pytest.mark.asyncio
async def test_get_totp_status_remote_envia_token_interno_y_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "signing_key_internal_token", "tok-interno-t8")
    vistas = _wire(
        monkeypatch, lambda r: httpx.Response(200, json={"activo": True, "user_id": str(USER)})
    )
    body = await get_totp_status_remote("Bearer tok")
    assert body == {"activo": True, "user_id": str(USER)}
    (req,) = vistas
    assert req.method == "GET" and req.url.path == "/internal/totp/status"
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["X-Internal-Token"] == "tok-interno-t8"


@pytest.mark.asyncio
async def test_verify_totp_envia_token_interno_y_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "signing_key_internal_token", "tok-interno-t8")
    vistas = _wire(monkeypatch, lambda r: httpx.Response(200, json={"valido": True}))
    body = await http_clients.verify_totp("Bearer tok", "123456")
    assert body == {"valido": True}
    (req,) = vistas
    assert req.method == "POST" and req.url.path == "/internal/totp/verify"
    assert req.headers["Authorization"] == "Bearer tok"
    # Sin el token interno auth-service responde 401 y la firma con TOTP queda
    # caída (fail-closed): el cable debe llevarlo.
    assert req.headers["X-Internal-Token"] == "tok-interno-t8"


@pytest.mark.asyncio
async def test_get_totp_status_remote_sin_authorization_no_sale_a_la_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vistas = _wire(monkeypatch, lambda r: httpx.Response(200, json={"activo": False}))
    with pytest.raises(TotpStatusUnavailableError):
        await get_totp_status_remote(None)
    assert vistas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(401, json={"detail": "x"}),
        httpx.Response(403, json={"detail": "x"}),
        httpx.Response(200, text="no es json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"user_id": str(USER)}),  # falta «activo»
    ],
)
async def test_get_totp_status_remote_todo_lo_raro_es_indisponible(
    monkeypatch: pytest.MonkeyPatch, respuesta: httpx.Response
) -> None:
    """Fail-closed: «no sé» no se lee como «no tiene TOTP»."""
    _wire(monkeypatch, lambda r: respuesta)
    with pytest.raises(TotpStatusUnavailableError):
        await get_totp_status_remote("Bearer tok")


@pytest.mark.asyncio
async def test_get_totp_status_remote_red_caida_es_indisponible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin ruta", request=request)

    _wire(monkeypatch, _boom)
    with pytest.raises(TotpStatusUnavailableError):
        await get_totp_status_remote("Bearer tok")


# ---------------------------------------------------------------------------
# 2. key_version en la cadena
# ---------------------------------------------------------------------------

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


def _conn_firma(challenge_row: dict):
    conn = _make_conn()
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(), {}, turno, turno, challenge_row, _FIRMA_INS, None,
        _sol_row(estado="firmado"), {},
    ]
    conn.fetch.return_value = [turno_firmado]
    conn.execute.side_effect = _exec_sig
    return conn


async def _firmar(conn, otp: str = "123456"):
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            return await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={"challenge_id": str(CHALLENGE_ID), "otp": otp},
                headers=TENANT_HEADERS,
            )
    finally:
        _clear()


def _legacy_row(otp: str = "123456") -> dict:
    row = _challenge_row(otp=otp)
    row["key_version"] = KEY_VERSION_LEGACY
    row["otp_hash"] = hmac_otp(
        otp,
        settings.signature_secret,
        tenant_slug="demo",
        challenge_id=CHALLENGE_ID,
        key_version=KEY_VERSION_LEGACY,
    )
    return row


@pytest.mark.asyncio
async def test_desafio_key_version_0_en_vuelo_sigue_verificando_en_la_cadena(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un desafío emitido antes de la 020 (vive minutos) debe poder consumirse:
    ``_gate_2fa`` verifica con la ``key_version`` DE LA FILA, no con la actual."""
    _patch_audit(monkeypatch)
    resp = await _firmar(_conn_firma(_legacy_row()))
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_desafio_cuya_key_version_no_corresponde_al_hash_se_rechaza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control negativo del anterior: la fila dice v1 pero el hash es de v0 -> 401."""
    _patch_audit(monkeypatch)
    row = _legacy_row()
    row["key_version"] = KEY_VERSION_ACTUAL
    resp = await _firmar(_conn_firma(row))
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_la_firma_nativa_de_la_cadena_se_persiste_con_la_key_version_actual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El INSERT de ``firma`` lleva la versión con que se calculó el HMAC; un 0
    literal haría que ``/verify`` recomputara con el esquema viejo y todas las
    firmas nuevas de la cadena dejaran de verificar."""
    _patch_audit(monkeypatch)
    conn = _conn_firma(_challenge_row())
    resp = await _firmar(conn)
    assert resp.status_code == 200, resp.text
    inserts = [
        c for c in conn.fetchrow.call_args_list if re.search(r"INSERT INTO firma\s*\(", str(c.args[0]))
    ]
    assert len(inserts) == 1
    assert inserts[0].args[-1] == KEY_VERSION_ACTUAL


@pytest.mark.asyncio
async def test_emitir_challenge_persiste_hash_que_verifica_con_su_propio_id_y_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El OTP que sale por correo debe verificar contra el ``otp_hash`` que se
    insertó, con el ``challenge_id`` insertado y la ``key_version`` insertada
    (el id se genera ANTES del hash: si no coinciden, el desafío nace inservible)."""
    from app.core.otp import verify_otp

    _patch_audit(monkeypatch)
    _, mock_send = _patch_email(monkeypatch)
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        _sol_row(), {}, _turno_row(firmante_id=USER), _sol_row(), _turno_row(firmante_id=USER),
        None, _challenge_row(),
    ]
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/challenge",
                headers={**TENANT_HEADERS, "Authorization": "Bearer t"},
            )
    finally:
        _clear()
    assert resp.status_code == 201, resp.text
    (ins,) = [
        c for c in conn.fetchrow.call_args_list if "INSERT INTO firma_challenge" in str(c.args[0])
    ]
    cid, kv, otp_hash = ins.args[1], ins.args[2], ins.args[6]
    assert isinstance(cid, uuid.UUID) and kv == KEY_VERSION_ACTUAL
    # Lo enviado por correo es el OTP en claro (3.er argumento de send_otp_email o kw).
    call = mock_send.await_args
    otp = next(
        v for v in list(call.args) + list(call.kwargs.values())
        if isinstance(v, str) and re.fullmatch(rf"\d{{{settings.otp_longitud}}}", v)
    )
    assert verify_otp(
        otp, otp_hash, settings.signature_secret,
        key_version=kv, tenant_slug="demo", challenge_id=cid,
    )
