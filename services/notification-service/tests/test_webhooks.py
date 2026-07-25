# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de webhooks salientes (E11, ADR-018)."""

import uuid
from datetime import UTC, datetime

import pytest

from app.services.webhook_service import sign
from tests.conftest import USER

NOW = datetime.now(tz=UTC)


def test_sign_hmac_deterministic() -> None:
    body = b'{"event":"x"}'
    s1 = sign("secret", body)
    s2 = sign("secret", body)
    assert s1 == s2
    assert s1.startswith("sha256=")
    assert sign("otra", body) != s1


@pytest.mark.asyncio
async def test_create_webhook(client, db_conn) -> None:
    wid = uuid.uuid4()
    db_conn.fetchrow.return_value = {
        "id": wid,
        "url": "https://ext.example.com/hook",
        "event_types": ["document.radicado.created"],
        "active": True,
        "created_at": NOW,
    }
    resp = await client.post(
        "/api/v1/webhooks",
        json={
            "url": "https://ext.example.com/hook",
            "event_types": ["document.radicado.created"],
            "secret": "s3cr3t",
        },
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"] == "https://ext.example.com/hook"
    assert body["event_types"] == ["document.radicado.created"]
    # El secret no se expone en la respuesta.
    assert "secret" not in body


@pytest.mark.asyncio
async def test_create_webhook_sin_permiso_403(client, db_conn) -> None:
    """Sin USUA_PERM_ADMIN no se registra un webhook (fail-closed).

    Registrar una suscripción entrega CADA evento del tenant a una URL externa:
    sin este gate era un canal de exfiltración (y SSRF) abierto a cualquier
    usuario autenticado. El gate es de router → cubre create/list/delete.
    """
    db_conn.fetchval.return_value = None  # check_permission → False
    resp = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://ext.example.com/hook", "event_types": ["document.radicado.created"]},
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_webhooks(client, db_conn) -> None:
    db_conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "url": "https://a.example/h",
            "event_types": [],
            "active": True,
            "created_at": NOW,
        },
    ]
    resp = await client.get("/api/v1/webhooks", headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)})
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "1"
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_delete_webhook_not_found(client, db_conn) -> None:
    db_conn.execute.return_value = "DELETE 0"
    resp = await client.delete(
        f"/api/v1/webhooks/{uuid.uuid4()}", headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_no_subscriptions_is_noop(db_conn, monkeypatch) -> None:
    """dispatch sin suscripciones no falla y no entrega nada."""
    from app.services import webhook_service

    class _Acq:
        async def __aenter__(self):
            return db_conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    db_conn.fetch.return_value = []  # active_for_event → ninguna

    n = await webhook_service.dispatch(
        {"tenant_slug": "demo", "event_type": "document.radicado.created", "payload": {}}
    )
    assert n == 0
