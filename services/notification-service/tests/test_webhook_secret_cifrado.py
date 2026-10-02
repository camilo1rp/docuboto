# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

"""El secreto HMAC de los webhooks se guarda CIFRADO y nunca sale.

Cubre: primitivas (AAD, KEK, nonce), que crear cifre y no guarde el claro, que
ninguna respuesta lo devuelva, y que la entrega descifre solo para firmar —y
falle cerrada si no puede—.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.exceptions import InvalidTag

from app.core import webhook_secrets
from app.core.config import settings
from app.services import webhook_service
from tests.conftest import TEST_WEBHOOK_KEK_V2, USER

SECRETO = "s3cr3t-HMAC-muy-reconocible"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
HEADERS = {"X-Tenant-Slug": "mi-tenant", "X-User-Id": str(USER)}


# ── primitivas ─────────────────────────────────────────────────────────────
def test_cifrar_y_descifrar_ida_y_vuelta() -> None:
    wid = uuid.uuid4()
    ct, nonce, version = webhook_secrets.encrypt_secret(
        SECRETO, tenant_slug="mi-tenant", webhook_id=wid
    )
    assert version == 1
    assert SECRETO.encode() not in ct
    assert len(nonce) == 12
    assert (
        webhook_secrets.decrypt_secret(
            ct, nonce=nonce, version=version, tenant_slug="mi-tenant", webhook_id=wid
        )
        == SECRETO
    )


def test_el_nonce_es_nuevo_en_cada_cifrado() -> None:
    wid = uuid.uuid4()
    a = webhook_secrets.encrypt_secret(SECRETO, tenant_slug="t", webhook_id=wid)
    b = webhook_secrets.encrypt_secret(SECRETO, tenant_slug="t", webhook_id=wid)
    assert a[1] != b[1] and a[0] != b[0]


def test_aad_de_otro_tenant_no_descifra() -> None:
    wid = uuid.uuid4()
    ct, nonce, v = webhook_secrets.encrypt_secret(SECRETO, tenant_slug="a", webhook_id=wid)
    with pytest.raises(InvalidTag):
        webhook_secrets.decrypt_secret(
            ct, nonce=nonce, version=v, tenant_slug="b", webhook_id=wid
        )


def test_aad_de_otro_webhook_no_descifra() -> None:
    ct, nonce, v = webhook_secrets.encrypt_secret(
        SECRETO, tenant_slug="a", webhook_id=uuid.uuid4()
    )
    with pytest.raises(InvalidTag):
        webhook_secrets.decrypt_secret(
            ct, nonce=nonce, version=v, tenant_slug="a", webhook_id=uuid.uuid4()
        )


def test_kek_equivocada_no_descifra() -> None:
    wid = uuid.uuid4()
    ct, nonce, v = webhook_secrets.encrypt_secret(SECRETO, tenant_slug="a", webhook_id=wid)
    settings.webhook_secret_kek = TEST_WEBHOOK_KEK_V2  # otra KEK bajo la misma versión
    with pytest.raises(InvalidTag):
        webhook_secrets.decrypt_secret(
            ct, nonce=nonce, version=v, tenant_slug="a", webhook_id=wid
        )


def test_version_de_kek_no_cargada_falla_cerrado() -> None:
    wid = uuid.uuid4()
    ct, nonce, _ = webhook_secrets.encrypt_secret(SECRETO, tenant_slug="a", webhook_id=wid)
    with pytest.raises(RuntimeError, match="no cargada"):
        webhook_secrets.decrypt_secret(
            ct, nonce=nonce, version=7, tenant_slug="a", webhook_id=wid
        )


def test_el_aad_ata_tenant_y_webhook() -> None:
    wid = uuid.uuid4()
    assert webhook_secrets.build_aad(tenant_slug="mi-tenant", webhook_id=wid) == (
        f"tenant:mi-tenant:webhook:{wid}:secret".encode()
    )


def test_slug_from_schema_es_inverso_de_tenant_schema() -> None:
    from app.core.database import tenant_schema

    for slug in ("icetex", "mi-tenant", "a-b-c", "x1-y2"):
        assert webhook_secrets.slug_from_schema(tenant_schema(slug)) == slug


def test_arranque_sin_kek_o_kek_invalida_falla() -> None:
    settings.webhook_secret_kek = ""
    with pytest.raises(RuntimeError):
        webhook_secrets.validate_kek_at_startup()
    settings.webhook_secret_kek = "no-es-base64!!"
    with pytest.raises(RuntimeError, match="base64"):
        webhook_secrets.validate_kek_at_startup()
    settings.webhook_secret_kek = "AAAA"  # base64 válido pero no 32 bytes
    with pytest.raises(RuntimeError, match="32 bytes"):
        webhook_secrets.validate_kek_at_startup()


def test_arranque_con_version_activa_sin_kek_falla() -> None:
    settings.webhook_secret_kek_active_version = 2
    with pytest.raises(RuntimeError, match="no cargada"):
        webhook_secrets.validate_kek_at_startup()


def test_la_v1_no_se_redefine_en_el_json() -> None:
    settings.webhook_secret_keks = {1: TEST_WEBHOOK_KEK_V2}
    with pytest.raises(RuntimeError, match="reservada"):
        webhook_secrets.keyring()


# ── crear: cifra, no guarda el claro, no lo devuelve ───────────────────────
# ── un secreto inválido no se devuelve en el 422 ───────────────────────────
@pytest.mark.asyncio
async def test_secreto_demasiado_largo_no_se_devuelve_en_el_422(client, db_conn) -> None:
    """El 422 de pydantic incluye `input` (el valor recibido): con un secreto de más de
    255 caracteres devolvía el secreto en claro al cliente (y a su log/proxy)."""
    largo = "S3creto-" * 40  # 320 caracteres
    resp = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://ext.example.com/hook", "secret": largo},
        headers=HEADERS,
    )
    assert resp.status_code == 422
    assert largo not in resp.text and "S3creto-" not in resp.text
    assert all("input" not in e for e in resp.json()["detail"])
    assert any(e["loc"][-1] == "secret" for e in resp.json()["detail"]), "el motivo se conserva"
    db_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_crear_guarda_cifrado_y_no_devuelve_el_secreto(client, db_conn, monkeypatch) -> None:
    audit = AsyncMock()
    monkeypatch.setattr("app.routers.webhooks._audit.append", audit)
    db_conn.fetchrow.side_effect = lambda sql, wid, url, et, *_: {
        "id": wid, "url": url, "event_types": et, "active": True, "created_at": NOW,
    }
    resp = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://ext.example.com/hook", "secret": SECRETO},
        headers=HEADERS,
    )
    assert resp.status_code == 201
    assert SECRETO not in resp.text and "secret" not in resp.json()

    sql, wid, _url, _et, ct, nonce, version = db_conn.fetchrow.await_args.args
    assert "secret_cifrado" in sql
    assert " secret," not in sql and "(secret" not in sql, "el INSERT toca la columna en claro"
    assert SECRETO.encode() not in ct and version == 1
    # Descifra con el AAD (tenant de la cabecera + id acuñado) y da el original.
    assert (
        webhook_secrets.decrypt_secret(
            ct, nonce=nonce, version=version, tenant_slug="mi-tenant", webhook_id=wid
        )
        == SECRETO
    )
    assert resp.json()["id"] == str(wid)
    # Ni el asiento ni ningún argumento del INSERT lleva el claro.
    assert SECRETO not in str(audit.await_args.kwargs)
    assert all(SECRETO not in str(a) for a in db_conn.fetchrow.await_args.args)


@pytest.mark.asyncio
async def test_crear_sin_secreto_no_cifra(client, db_conn, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.webhooks._audit.append", AsyncMock())
    db_conn.fetchrow.side_effect = lambda sql, wid, url, et, *_: {
        "id": wid, "url": url, "event_types": et, "active": True, "created_at": NOW,
    }
    resp = await client.post(
        "/api/v1/webhooks", json={"url": "https://ext.example.com/hook"}, headers=HEADERS
    )
    assert resp.status_code == 201
    *_, ct, nonce, version = db_conn.fetchrow.await_args.args
    assert (ct, nonce, version) == (None, None, None)


@pytest.mark.asyncio
async def test_crear_con_secreto_sin_kek_es_503_y_no_escribe(client, db_conn, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.webhooks._audit.append", AsyncMock())
    settings.webhook_secret_kek = ""
    resp = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://ext.example.com/hook", "secret": SECRETO},
        headers=HEADERS,
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "webhook_secret_kek_unavailable"
    assert SECRETO not in resp.text
    db_conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_listar_nunca_devuelve_el_secreto(client, db_conn) -> None:
    """Ni la consulta lo selecciona ni el esquema de respuesta lo admite: aunque
    una fila trajera el claro o el cifrado, no sale."""
    fila = {
        "id": uuid.uuid4(), "url": "https://ext.example.com/hook",
        "event_types": [], "active": True, "created_at": NOW,
        "secret": SECRETO, "secret_cifrado": b"\x01\x02", "secret_nonce": b"n",
        "secret_kek_version": 1,
    }
    db_conn.fetch.return_value = [fila]
    resp = await client.get("/api/v1/webhooks", headers=HEADERS)
    assert resp.status_code == 200
    assert SECRETO not in resp.text
    assert set(resp.json()[0]) == {"id", "url", "event_types", "active", "created_at"}
    sql = db_conn.fetch.await_args.args[0]
    assert "secret" not in sql


# ── entrega: descifra en memoria para firmar; fail-closed ──────────────────
async def _entregar(sub: dict, tenant: str = "mi-tenant") -> tuple[bool, list[httpx.Request]]:
    vistas: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_h)) as c:
        ok = await webhook_service._deliver_one(c, sub, b'{"a":1}', tenant)
    return ok, vistas


@pytest.fixture
def _dns(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


def _sub_cifrada(wid: uuid.UUID, tenant: str = "mi-tenant") -> dict:
    ct, nonce, v = webhook_secrets.encrypt_secret(SECRETO, tenant_slug=tenant, webhook_id=wid)
    return {
        "id": wid, "url": "https://hook.example.com/p", "secret_cifrado": ct,
        "secret_nonce": nonce, "secret_kek_version": v, "secret_en_claro": False,
    }


@pytest.mark.asyncio
async def test_la_entrega_firma_con_el_secreto_original(_dns) -> None:
    ok, vistas = await _entregar(_sub_cifrada(uuid.uuid4()))
    assert ok and len(vistas) == 1
    esperado = "sha256=" + hmac.new(SECRETO.encode(), b'{"a":1}', hashlib.sha256).hexdigest()
    assert vistas[0].headers["X-OrpycaMCP-Signature"] == esperado


@pytest.mark.asyncio
async def test_sin_secreto_se_entrega_sin_firma(_dns) -> None:
    ok, vistas = await _entregar({"id": uuid.uuid4(), "url": "https://hook.example.com/p"})
    assert ok and "X-OrpycaMCP-Signature" not in vistas[0].headers


@pytest.mark.asyncio
async def test_fila_en_claro_no_se_entrega_ni_se_firma_con_ese_texto(_dns, caplog) -> None:
    ok, vistas = await _entregar(
        {"id": uuid.uuid4(), "url": "https://hook.example.com/p", "secret_cifrado": None,
         "secret_en_claro": True}
    )
    assert not ok and vistas == []
    assert "falta cifrar" in caplog.text


@pytest.mark.asyncio
async def test_ciphertext_de_otro_tenant_no_se_entrega(_dns, caplog) -> None:
    ok, vistas = await _entregar(_sub_cifrada(uuid.uuid4()), tenant="otro")
    assert not ok and vistas == []
    assert SECRETO not in caplog.text


@pytest.mark.asyncio
async def test_kek_perdida_no_se_entrega_y_no_filtra(_dns, caplog) -> None:
    sub = _sub_cifrada(uuid.uuid4())
    settings.webhook_secret_kek = TEST_WEBHOOK_KEK_V2
    ok, vistas = await _entregar(sub)
    assert not ok and vistas == []
    assert SECRETO not in caplog.text and "InvalidTag" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_pasa_el_tenant_del_sobre_al_aad(monkeypatch, _dns) -> None:
    wid = uuid.uuid4()
    sub = _sub_cifrada(wid, tenant="mi-tenant")

    class _Acq:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.services.webhook_service.acquire_tenant", lambda slug: _Acq())
    monkeypatch.setattr(
        "app.services.webhook_service.WebhookRepository.active_for_event",
        AsyncMock(return_value=[sub]),
    )
    vistas: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _cli(**kw):
        return real(transport=httpx.MockTransport(lambda r: (vistas.append(r), httpx.Response(200))[1]))

    monkeypatch.setattr("app.services.webhook_service.httpx.AsyncClient", _cli)
    env = {"tenant_slug": "mi-tenant", "event_type": "x.y", "payload": {"a": 1}}
    assert await webhook_service.dispatch(env) == 1
    assert len(vistas) == 1 and "X-OrpycaMCP-Signature" in vistas[0].headers
    # Mismo sobre pero otro tenant en el sobre: el AAD no casa, no se entrega.
    vistas.clear()
    assert await webhook_service.dispatch({**env, "tenant_slug": "otro"}) == 1
    assert vistas == []
    assert json.dumps(vistas) == "[]"
