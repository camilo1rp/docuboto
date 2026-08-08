# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Códigos de recuperación del 2FA TOTP (E06, migración 015).

Qué se cubre y por qué. La brecha que cierra esta funcionalidad es concreta:
todas las salidas del TOTP exigían un código TOTP, es decir, exigían el mismo
teléfono que se perdió. Así que los tests se centran en (1) que el código de
recuperación abra esas salidas, (2) que sea de UN SOLO uso, y (3) la frontera
que NO debe cruzar — firmar. Las primitivas (formato, normalización, hash) se
prueban aparte porque son puras.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.authz import Caller, get_caller
from app.core.config import settings
from app.core.database import get_tenant_conn
from app.core.recovery_codes import (
    generate_recovery_code,
    generate_recovery_codes,
    hash_recovery_code,
    new_salt,
    normalize_recovery_code,
    verify_recovery_code,
)
from app.core.totp import (
    _hotp,
    build_aad,
    current_timestep,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
)
from app.main import app

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TOTP_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
RECOVERY_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
TENANT_HEADERS = {"X-Tenant-Slug": "demo", "X-User-Id": str(USER_ID)}
TEST_KEY = base64.b64encode(b"0" * 32).decode()


# ---------------------------------------------------------------------------
# Primitivas puras (app/core/recovery_codes.py)
# ---------------------------------------------------------------------------


def test_codigo_tiene_formato_legible_y_alfabeto_sin_ambiguos() -> None:
    code = generate_recovery_code()
    grupos = code.split("-")
    assert len(grupos) == 4
    assert all(len(g) == 5 for g in grupos)
    # Estos códigos se imprimen y se dictan: un `0` que se lee como `O` es un
    # modo de fallo real, así que el alfabeto los excluye.
    assert not set(code) & set("01ILO")


def test_lote_no_repite_codigos() -> None:
    codigos = generate_recovery_codes(10)
    assert len(set(codigos)) == 10


@pytest.mark.parametrize(
    "variante",
    ["abcde-fghjk-mnpqr-stuvw", "ABCDEFGHJKMNPQRSTUVW", "  abcde fghjk-mnpqr stuvw "],
)
def test_normalizacion_acepta_como_el_usuario_lo_teclea(variante: str) -> None:
    """Guiones opcionales, espacios sueltos y minúsculas dan el MISMO hash.

    No es cortesía: un falso negativo al teclear un código correcto empuja al
    usuario justo hacia la intervención manual sobre la base de datos que esta
    funcionalidad existe para evitar.
    """
    canonico = "ABCDEFGHJKMNPQRSTUVW"
    assert normalize_recovery_code(variante) == canonico
    salt = new_salt()
    assert verify_recovery_code(variante, salt=salt, expected_hash=hash_recovery_code(canonico, salt=salt))


def test_hash_depende_de_la_sal() -> None:
    """Dos usuarios con el mismo código (improbable, pero posible) no comparten
    hash — sin sal por código, una tabla precomputada valdría para todos."""
    code = generate_recovery_code()
    assert hash_recovery_code(code, salt=new_salt()) != hash_recovery_code(code, salt=new_salt())


def test_codigo_incorrecto_no_verifica() -> None:
    salt = new_salt()
    expected = hash_recovery_code("ABCDE-FGHJK-MNPQR-STUVW", salt=salt)
    assert not verify_recovery_code("ABCDE-FGHJK-MNPQR-STUVX", salt=salt, expected_hash=expected)


# ---------------------------------------------------------------------------
# Integración por endpoint (mock de conexión, espejo de tests/test_totp.py)
# ---------------------------------------------------------------------------


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


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    conn.execute.return_value = "UPDATE 1"
    return conn


def _override(conn: AsyncMock) -> None:
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    app.dependency_overrides[get_caller] = lambda: Caller(
        user_id=USER_ID, keycloak_sub="kc-sub", is_root=False, permissions={}
    )


def _active_row() -> dict:
    secret = generate_secret()
    aad = build_aad(tenant_slug="demo", user_id=str(USER_ID))
    ciphertext, nonce = encrypt_secret(secret, key=settings.totp_encryption_key_bytes(), aad=aad)
    return {
        "id": TOTP_ID,
        "user_id": USER_ID,
        "secret_cifrado": ciphertext,
        "secret_nonce": nonce,
        "algoritmo": "SHA1",
        "digitos": 6,
        "periodo": 30,
        "estado": "activo",
        "last_timestep": 0,
        "failed_attempts": 0,
        "locked_until": None,
        "key_version": 1,
        "created_at": datetime.now(UTC),
        "activated_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


def _recovery_rows(code: str) -> list[dict]:
    salt = new_salt()
    return [{"id": RECOVERY_ID, "code_hash": hash_recovery_code(code, salt=salt), "code_salt": salt}]


@pytest.mark.asyncio
async def test_revocar_con_codigo_de_recuperacion(client: AsyncClient) -> None:
    """EL caso que la funcionalidad existe para resolver: revocar el TOTP sin
    tener el teléfono. Antes, `DELETE /me/totp` exigía un código TOTP vigente
    —el mismo dispositivo perdido— y el usuario quedaba encerrado."""
    conn = _make_conn()
    code = generate_recovery_code()
    conn.fetchrow.side_effect = [
        _active_row(),  # get_by_user
        {"id": RECOVERY_ID},  # consume_recovery_code -> RETURNING id
    ]
    conn.fetch.side_effect = [_recovery_rows(code)]
    _override(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/totp",
            json={"step_up_recovery_code": code},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "revocado"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_revocar_con_codigo_de_recuperacion_ya_usado_falla(client: AsyncClient) -> None:
    """Un solo uso: si el consumo no devuelve fila (ya consumido, o perdió la
    carrera contra otra petición simultánea), el código NO vale.

    LÍMITE DECLARADO de este test: con la conexión mockeada, el `None` lo
    devuelve el mock, no la base — así que esto prueba que el SERVICIO trata
    "0 filas" como código inválido, y **no** que el SQL lleve la condición que
    produce esas 0 filas. Esa parte se pin-ea en
    `test_el_consumo_es_un_update_condicional` (abajo) y su comportamiento
    real bajo concurrencia pertenece al carril de integración: quitar el
    `WHERE used_at IS NULL` no rompe este test, y comprobarlo con una mutación
    fue lo que destapó el hueco.
    """
    conn = _make_conn()
    code = generate_recovery_code()
    conn.fetchrow.side_effect = [_active_row(), None]  # consume -> 0 filas
    conn.fetch.side_effect = [_recovery_rows(code)]
    _override(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/totp",
            json={"step_up_recovery_code": code},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "step_up_invalido"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_el_consumo_es_un_update_condicional() -> None:
    """El SQL del consumo lleva `WHERE ... used_at IS NULL`.

    Es lo ÚNICO que impide que un código de un solo uso se gaste dos veces
    cuando dos peticiones llegan a la vez: sin esa condición ambas ven filas
    afectadas y ambas pasan el step-up. Con la conexión mockeada no hay
    concurrencia real que observar, así que se fija el contrato de la
    consulta — un test de forma, declarado como tal, en vez de uno de
    comportamiento que no comprueba lo que dice.
    """
    from app.repositories.totp_repository import TotpRepository

    conn = _make_conn()
    conn.fetchrow.return_value = {"id": RECOVERY_ID}
    assert await TotpRepository(conn).consume_recovery_code(RECOVERY_ID) is True

    sql = " ".join(conn.fetchrow.call_args.args[0].split())
    assert "UPDATE auth_user_totp_recovery" in sql
    assert "WHERE id = $1 AND used_at IS NULL" in sql
    assert "RETURNING id" in sql


@pytest.mark.asyncio
async def test_revocar_con_codigo_de_recuperacion_ajeno_falla(client: AsyncClient) -> None:
    """Un código que no casa con ninguno de los emitidos es 401 — y el mensaje
    es el mismo `step_up_invalido` que un TOTP malo: no distingue "no tienes
    códigos" de "ese código no es tuyo"."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [_active_row()]
    conn.fetch.side_effect = [_recovery_rows(generate_recovery_code())]
    _override(conn)
    try:
        resp = await client.request(
            "DELETE",
            "/api/v1/auth/me/totp",
            json={"step_up_recovery_code": generate_recovery_code()},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reenrolar_con_codigo_de_recuperacion(client: AsyncClient) -> None:
    """La otra salida bloqueada: enrolar un teléfono NUEVO sobre un TOTP activo
    exigía un código del teléfono viejo."""
    conn = _make_conn()
    code = generate_recovery_code()
    nueva = _active_row()
    nueva["estado"] = "pendiente_activacion"
    conn.fetchrow.side_effect = [
        _active_row(),  # get_by_user
        {"id": RECOVERY_ID},  # consume
        nueva,  # insert_pending
        {"id": USER_ID, "username": "operador", "email": "op@demo"},  # get_user
    ]
    conn.fetch.side_effect = [_recovery_rows(code)]
    _override(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll",
            json={"step_up_recovery_code": code},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["estado"] == "pendiente_activacion"
        # El secreto viaja en el otpauth:// — ninguna caché debe guardarlo.
        assert resp.headers["Cache-Control"] == "no-store"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reenrolar_sin_ningun_factor_sigue_dando_409(client: AsyncClient) -> None:
    """Control negativo: añadir el código de recuperación NO abrió una tercera
    puerta sin factor alguno."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [_active_row()]
    _override(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/enroll", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "totp_ya_activo"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_regenerar_codigos_devuelve_un_lote_nuevo(client: AsyncClient) -> None:
    conn = _make_conn()
    code = generate_recovery_code()
    conn.fetchrow.side_effect = [_active_row(), {"id": RECOVERY_ID}]
    conn.fetch.side_effect = [_recovery_rows(code)]
    _override(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/recovery-codes",
            json={"step_up_recovery_code": code},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code == 200
        codigos = resp.json()["recovery_codes"]
        assert len(codigos) == 10
        assert len(set(codigos)) == 10
        # Se muestran una sola vez: la respuesta no puede quedar en caché.
        assert resp.headers["Cache-Control"] == "no-store"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_regenerar_sin_step_up_falla(client: AsyncClient) -> None:
    """Regenerar invalida el lote guardado, así que desde una sesión
    secuestrada dejaría al titular sin vía de recuperación: exige factor."""
    conn = _make_conn()
    conn.fetchrow.side_effect = [_active_row()]
    conn.fetch.side_effect = [[]]
    _override(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/recovery-codes", json={}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_activar_emite_los_codigos_una_sola_vez(client: AsyncClient) -> None:
    """Se emiten AL ACTIVAR, no al enrolar: hasta entonces la credencial no
    protege nada. Y van en la misma respuesta, la única vez que existen fuera
    de la memoria del usuario."""
    conn = _make_conn()
    row = _active_row()
    row["estado"] = "pendiente_activacion"
    row["activated_at"] = None
    # El código válido se recalcula a partir del secreto real de la fila (el
    # mismo camino que `_valid_code` en tests/test_totp.py).
    secreto_plano = decrypt_secret(
        row["secret_cifrado"],
        nonce=row["secret_nonce"],
        key=settings.totp_encryption_key_bytes(),
        aad=build_aad(tenant_slug="demo", user_id=str(USER_ID)),
    )
    code = _hotp(secreto_plano, current_timestep())

    activada = dict(row)
    activada["estado"] = "activo"
    activada["activated_at"] = datetime.now(UTC)
    conn.fetchrow.side_effect = [row, activada]
    _override(conn)
    try:
        resp = await client.post(
            "/api/v1/auth/me/totp/activate", json={"code": code}, headers=TENANT_HEADERS
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["estado"] == "activo"
        assert len(body["recovery_codes"]) == 10
        assert resp.headers["Cache-Control"] == "no-store"
    finally:
        app.dependency_overrides.clear()


def test_el_verify_interno_no_acepta_codigos_de_recuperacion() -> None:
    """FRONTERA (deliberada): el segundo factor de la FIRMA no admite códigos
    de recuperación.

    Un código de recuperación está escrito en papel o en un gestor: no es
    posesión de un dispositivo, y admitirlo al firmar debilitaría la
    atribución que RF-FIR-13 persigue. Recuperar el acceso y firmar son actos
    distintos y se exigen pruebas distintas. El control es estructural: el
    cuerpo del endpoint interno no tiene campo donde meterlo, y su `code` está
    validado como TOTP (6 dígitos).
    """
    from app.schemas.totp import InternalTotpVerifyRequest

    assert "recovery" not in str(InternalTotpVerifyRequest.model_fields.keys())
    with pytest.raises(ValueError):
        InternalTotpVerifyRequest(code=generate_recovery_code())
