# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Restablecimiento ADMINISTRATIVO del segundo factor — `POST /users/{id}/totp/revoke`.

El hueco que cierra: quien perdía el autenticador quedaba encerrado. `enroll` y
`revoke_self` exigen el código actual o uno de recuperación, los códigos de
recuperación NO sirven para firmar (`verify` solo clasifica un TOTP), y no
existía ninguna vía administrativa — la única salida era un UPDATE a mano en la
base de datos.

Lo que estos tests fijan, y que es donde está el riesgo de la funcionalidad:
el asiento se atribuye al ADMINISTRADOR y no a la víctima, los códigos de
recuperación viejos mueren con la credencial, y nadie puede usarlo sobre sí
mismo (separación de funciones: hacen falta dos personas).
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.config import settings
from app.core.database import get_tenant_conn
from app.core.totp import build_aad, encrypt_secret
from app.main import app

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
ADMIN_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
VICTIMA_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
TOTP_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
TEST_KEY = base64.b64encode(b"0" * 32).decode()
MOTIVO = "Perdió el teléfono; verificado en persona el 20/08/2026"


class _FakeTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _totp_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "totp_encryption_key", TEST_KEY)


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_append = AsyncMock(return_value="fakehash")
    monkeypatch.setattr("app.services.totp_service._audit.append", mock_append)
    return mock_append


def _make_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    conn.execute.return_value = "UPDATE 1"
    return conn


def _admin(permissions: dict | None = None) -> Caller:
    return Caller(
        user_id=ADMIN_ID,
        keycloak_sub="kc-admin",
        is_root=False,
        permissions=permissions if permissions is not None else {"USUA_PERM_ADMIN": 3},
    )


def _override(conn: AsyncMock, caller: Caller) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    app.dependency_overrides[get_caller] = lambda: caller


def _clear() -> None:
    app.dependency_overrides.clear()


def _totp_row(*, estado: str = "activo", user_id: uuid.UUID = VICTIMA_ID) -> dict:
    ciphertext, nonce = encrypt_secret(
        b"0123456789",
        key=base64.b64decode(TEST_KEY),
        aad=build_aad(tenant_slug="demo", user_id=str(user_id)),
    )
    return {
        "id": TOTP_ID,
        "user_id": user_id,
        "secret_cifrado": ciphertext,
        "secret_nonce": nonce,
        "algoritmo": "SHA1",
        "digitos": 6,
        "periodo": 30,
        "estado": estado,
        "last_timestep": 0,
        "failed_attempts": 0,
        "locked_until": None,
        "key_version": 1,
        "created_at": datetime.now(UTC),
        "activated_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


async def _post(client: AsyncClient, user_id: uuid.UUID, **body: object):
    return await client.post(
        f"/api/v1/auth/users/{user_id}/totp/revoke",
        json={"motivo": MOTIVO, **body},
        headers=TENANT_HEADERS,
    )


@pytest.mark.asyncio
async def test_restablece_y_deja_a_la_persona_lista_para_reenrolar(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = _totp_row()
    conn.fetchval.return_value = 7  # códigos de recuperación aún sin usar
    _override(conn, _admin())
    try:
        resp = await _post(client, VICTIMA_ID)
    finally:
        _clear()

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["estado"] == "revocado"
    assert cuerpo["usuario_id"] == str(VICTIMA_ID)
    assert cuerpo["estado_previo"] == "activo"
    assert cuerpo["codigos_recuperacion_eliminados"] == 7

    # NUNCA devuelve secreto ni enrola por otro: quien pudiera enrolar en nombre
    # ajeno tendría de hecho la firma personal de esa persona.
    assert "otpauth_uri" not in cuerpo
    assert "recovery_codes" not in cuerpo


@pytest.mark.asyncio
async def test_el_asiento_se_atribuye_al_administrador_no_a_la_victima(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    """El punto entero de la trazabilidad de este acto.

    `_audit_event` fijaba `actor=user_id` sin alternativa, lo cual es correcto
    en todo el auto-servicio pero aquí produciría un asiento que dice que la
    persona se retiró el factor a sí misma — falso, y precisamente en el único
    acto en que alguien reduce la protección de otro.
    """
    conn = _make_conn()
    conn.fetchrow.return_value = _totp_row()
    conn.fetchval.return_value = 3
    _override(conn, _admin())
    try:
        resp = await _post(client, VICTIMA_ID)
    finally:
        _clear()
    assert resp.status_code == 200, resp.text

    _patch_audit.assert_awaited_once()
    kw = _patch_audit.await_args.kwargs
    assert kw["action"] == "totp.restablecido_por_admin"
    assert kw["actor"] == str(ADMIN_ID), "el actor es quien restablece"
    assert kw["object_ref"] == str(VICTIMA_ID), "el objeto es quien lo padece"
    assert kw["payload"]["motivo"] == MOTIVO
    assert kw["payload"]["estado_previo"] == "activo"
    assert kw["payload"]["codigos_recuperacion_eliminados"] == 3
    # El secreto jamás entra en un asiento.
    serializado = str(kw["payload"])
    assert "secret" not in serializado and "cifrado" not in serializado


@pytest.mark.asyncio
async def test_los_codigos_de_recuperacion_mueren_con_la_credencial(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    """Dejarlos vivos sería peor que no restablecer: quedarían apuntando a una
    credencial muerta, porque la fila se marca 'revocado' en vez de borrarse y
    el CASCADE de la migración 015 no llega a dispararse."""
    conn = _make_conn()
    conn.fetchrow.return_value = _totp_row()
    conn.fetchval.return_value = 10
    _override(conn, _admin())
    try:
        resp = await _post(client, VICTIMA_ID)
    finally:
        _clear()
    assert resp.status_code == 200, resp.text

    sentencias = [str(c.args[0]).lower() for c in conn.execute.await_args_list if c.args]
    assert any(
        "delete from auth_user_totp_recovery" in q for q in sentencias
    ), "no se destruyeron los códigos de recuperación"
    assert any(
        "auth_user_totp" in q and "revocado" in q for q in sentencias
    ), "no se revocó la credencial"


@pytest.mark.asyncio
async def test_no_se_puede_restablecer_el_propio(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    """Separación de funciones. Un administrador que se restableciera el suyo
    podría retirárselo sin demostrar posesión del dispositivo y enrolar otro
    acto seguido: quien robase su sesión se quedaría con el segundo factor de
    su firma personal. Aquí hacen falta dos personas siempre."""
    conn = _make_conn()
    conn.fetchrow.return_value = _totp_row(user_id=ADMIN_ID)
    conn.fetchval.return_value = 5
    _override(conn, _admin())
    try:
        resp = await _post(client, ADMIN_ID)
    finally:
        _clear()

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "restablecimiento_propio_no_permitido"
    # Y no toca nada: ni revoca, ni borra códigos, ni deja asiento.
    conn.execute.assert_not_awaited()
    _patch_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_credencial_responde_404_y_no_deja_asiento(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = None
    _override(conn, _admin())
    try:
        resp = await _post(client, VICTIMA_ID)
    finally:
        _clear()

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "no_totp"
    _patch_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_permiso_administrativo_no_pasa(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    """El gate va en el router, como el resto de `/users`. Sin él, cualquier
    usuario autenticado podría dejar a otro sin segundo factor."""
    conn = _make_conn()
    conn.fetchrow.return_value = _totp_row()
    _override(conn, _admin(permissions={"PERM_RADI": 3}))
    try:
        resp = await _post(client, VICTIMA_ID)
    finally:
        _clear()

    assert resp.status_code == 403, resp.text
    # OJO: sí hay asiento, y debe haberlo — `require_permission` traza el acceso
    # denegado (RF-SEG). Lo que no puede existir es el asiento del acto: exigir
    # "ninguna llamada" aquí probaría lo contrario de lo que se quiere.
    acciones = [c.kwargs.get("action") for c in _patch_audit.await_args_list]
    assert "totp.restablecido_por_admin" not in acciones
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_motivo_vacio_o_de_relleno_se_rechaza(
    client: AsyncClient, _patch_audit: AsyncMock
) -> None:
    """El motivo es lo que hace auditable el acto; un espacio en blanco lo
    convertiría en un campo que se rellena sin pensar."""
    conn = _make_conn()
    conn.fetchrow.return_value = _totp_row()
    _override(conn, _admin())
    try:
        vacio = await client.post(
            f"/api/v1/auth/users/{VICTIMA_ID}/totp/revoke",
            json={"motivo": "         "},
            headers=TENANT_HEADERS,
        )
        corto = await client.post(
            f"/api/v1/auth/users/{VICTIMA_ID}/totp/revoke",
            json={"motivo": "perdio"},
            headers=TENANT_HEADERS,
        )
    finally:
        _clear()

    assert vacio.status_code == 422, vacio.text
    assert corto.status_code == 422, corto.text
    _patch_audit.assert_not_awaited()
