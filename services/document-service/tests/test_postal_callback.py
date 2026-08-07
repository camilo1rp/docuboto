# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del webhook ENTRANTE del operador postal (E20 F5) + consulta por guía.

El callback vive bajo `/api/v1/public/postal/callback/{tenant_slug}` (el gateway
no exige JWT ahí); se autentica por HMAC per-tenant/per-operador. Usa
`app.state.db_pool` directamente (conn con search_path del slug del path), así que
la fixture `callback_ctx` monta un pool falso sobre el `mock_conn` compartido.
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.postal_webhook_auth import verify_signature
from app.main import app
from tests.conftest import (
    TENANT_HEADERS,
    FakeTenantClient,  # type: ignore[attr-defined]
    _make_fake_redis,
    _make_mock_conn,
    _override_conn,
)

NOW = datetime.now(tz=UTC)
SECRET = "operator-shared-secret"
SLUG = "demo"


def _ship_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "operador": "4-72",
        "guia": "C472-ABCDEF0123",
        "estado": "registrado",
        "destinatario": "Juan Pérez",
        "direccion": "Calle 1 #2-3",
        "motivo_devolucion": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _body(**kw) -> bytes:
    payload = {
        "operador": "4-72",
        "guia": "C472-ABCDEF0123",
        "estado_operador": "EN_TRANSITO",
        "event_id": "evt-001",
    }
    payload.update(kw)
    return json.dumps(payload).encode()


class _FakePool:
    """Pool mínimo cuyo `acquire()` es un CM async que cede el mock_conn."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _CM()


@pytest_asyncio.fixture
async def callback_ctx():
    """(client, mock_conn, fake_redis) con app.state.db_pool = pool falso."""
    mock_conn = _make_mock_conn()
    fake_redis = _make_fake_redis()
    _override_conn(mock_conn, FakeTenantClient())
    from app.core.database import get_redis

    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.db_pool = _FakePool(mock_conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_conn, fake_redis
    app.dependency_overrides.clear()
    if hasattr(app.state, "db_pool"):
        delattr(app.state, "db_pool")


def _url(slug: str = SLUG) -> str:
    return f"/api/v1/public/postal/callback/{slug}"


def _hdr(body: bytes, secret: str = SECRET) -> dict:
    return {"Content-Type": "application/json", "X-OrpycaMCP-Signature": _sign(secret, body)}


# ---------------------------------------------------------------------------
# verify_signature — unidad pura
# ---------------------------------------------------------------------------


def test_verify_signature_valid() -> None:
    body = b'{"a":1}'
    assert verify_signature(SECRET, body, _sign(SECRET, body)) is True


def test_verify_signature_wrong_secret() -> None:
    body = b'{"a":1}'
    assert verify_signature(SECRET, body, _sign("otro", body)) is False


def test_verify_signature_missing_prefix() -> None:
    body = b'{"a":1}'
    raw = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()  # sin "sha256="
    assert verify_signature(SECRET, body, raw) is False


def test_verify_signature_none_header() -> None:
    assert verify_signature(SECRET, b"x", None) is False


def test_verify_signature_empty_secret() -> None:
    body = b"x"
    assert verify_signature("", body, _sign("", body)) is False


# ---------------------------------------------------------------------------
# Callback — camino feliz e idempotencia
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_applies_valid_transition(callback_ctx) -> None:
    client, conn, redis = callback_ctx
    body = _body(estado_operador="EN_TRANSITO", event_id="evt-1")
    conn.fetchval.side_effect = [SECRET, uuid.uuid4(), None]  # cred, delivery_id, prev_hash
    conn.fetchrow.side_effect = [
        _ship_row(estado="registrado"),
        _ship_row(estado="en_transito"),
    ]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "applied"
    assert data["estado"] == "en_transito"
    redis.xadd.assert_awaited()  # evento de dominio emitido para E16


@pytest.mark.asyncio
async def test_callback_duplicate_event_is_noop(callback_ctx) -> None:
    client, conn, redis = callback_ctx
    body = _body(event_id="evt-dup")
    # try_insert_delivery devuelve None → ya procesado
    conn.fetchval.side_effect = [SECRET, None]
    conn.fetchrow.side_effect = [_ship_row(estado="registrado")]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicate"
    redis.xadd.assert_not_awaited()  # duplicado no re-emite


@pytest.mark.asyncio
async def test_callback_invalid_signature_401(callback_ctx) -> None:
    client, conn, _ = callback_ctx
    body = _body()
    conn.fetchval.side_effect = [SECRET]
    # firma con secreto equivocado
    bad = {"Content-Type": "application/json", "X-OrpycaMCP-Signature": _sign("otro", body)}
    resp = await client.post(_url(), content=body, headers=bad)
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "firma_invalida"


@pytest.mark.asyncio
async def test_callback_no_credential_401(callback_ctx) -> None:
    client, conn, _ = callback_ctx
    body = _body()
    conn.fetchval.side_effect = [None]  # sin credencial para el operador
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "firma_invalida"


@pytest.mark.asyncio
async def test_callback_unmapped_operator_status_422(callback_ctx) -> None:
    client, conn, _ = callback_ctx
    body = _body(estado_operador="ESTADO_MARCIANO")
    conn.fetchval.side_effect = [SECRET]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "estado_operador_no_mapeable"


@pytest.mark.asyncio
async def test_callback_unknown_guia_404(callback_ctx) -> None:
    client, conn, _ = callback_ctx
    body = _body(guia="C472-NOEXISTE")
    conn.fetchval.side_effect = [SECRET]
    conn.fetchrow.side_effect = [None]  # find_by_guia → None
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "envio_no_encontrado"


@pytest.mark.asyncio
async def test_callback_invalid_transition_recorded_no_change(callback_ctx) -> None:
    client, conn, redis = callback_ctx
    # operador reporta EN_TRANSITO sobre un envío ya ENTREGADO (terminal)
    body = _body(estado_operador="EN_TRANSITO", event_id="evt-late")
    conn.fetchval.side_effect = [SECRET, uuid.uuid4(), None]
    conn.fetchrow.side_effect = [_ship_row(estado="entregado")]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded_no_change"
    assert data["estado"] == "entregado"  # no regresa
    redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_devuelto_applies(callback_ctx) -> None:
    client, conn, redis = callback_ctx
    body = _body(estado_operador="DEVUELTO", event_id="evt-dev", motivo="Dirección errada")
    conn.fetchval.side_effect = [SECRET, uuid.uuid4(), None]
    conn.fetchrow.side_effect = [
        _ship_row(estado="en_transito"),
        _ship_row(estado="devuelto", motivo_devolucion="Dirección errada"),
    ]
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
    assert resp.json()["estado"] == "devuelto"


@pytest.mark.asyncio
async def test_callback_invalid_slug_400(callback_ctx) -> None:
    client, _, _ = callback_ctx
    body = _body()
    resp = await client.post(_url("Invalid Slug!"), content=body, headers=_hdr(body))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_bad_json_400(callback_ctx) -> None:
    client, conn, _ = callback_ctx
    body = b"{not json"
    conn.fetchval.side_effect = [SECRET]
    # firma correcta sobre bytes no-JSON: pasa HMAC pero falla el parseo estructural
    resp = await client.post(_url(), content=body, headers=_hdr(body))
    # el parseo ocurre ANTES de leer la credencial → 400 sin tocar la BD
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "cuerpo_invalido"


# ---------------------------------------------------------------------------
# Tracking (pull) — stub sin operador conectado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracking_stub_no_operator(client_with_conn) -> None:
    client, conn = client_with_conn
    sid = uuid.uuid4()
    conn.fetchval.return_value = True  # permiso
    conn.fetchrow.side_effect = [None, _ship_row(id=sid, estado="en_transito")]
    # resolve_caller_clearance usa fetchrow (None → PUBLICA) + repo.get (shipment)
    resp = await client.get(
        f"/api/v1/envios/{sid}/tracking",
        headers={**TENANT_HEADERS, "X-User-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["operador_conectado"] is False
    assert data["estado_operador"] is None
    assert data["estado_interno_actual"] == "en_transito"


@pytest.mark.asyncio
async def test_tracking_sin_permiso_403(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = None  # sin PERM_RADI_SALIDA
    resp = await client.get(
        f"/api/v1/envios/{uuid.uuid4()}/tracking",
        headers={**TENANT_HEADERS, "X-User-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403
