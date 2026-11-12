# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

"""Secreto HMAC de webhooks cifrado en reposo, contra Postgres REAL.

Un mock solo prueba que se LLAMA al cifrado; que la base no conserve el claro, que
el re-wrap a una KEK nueva no pierda el secreto y que la entrega salga firmada con
el secreto original lo dice únicamente la base.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio

from notification.core.config import settings
from notification.ops import cifrar_webhook_secrets as cifrar
from notification.ops import rewrap_webhook_secrets as rewrap
from notification.repositories.webhook_repository import WebhookRepository
from notification.routers import webhooks as router
from notification.schemas.webhook import WebhookCreate
from notification.services import webhook_service
from tests.conftest import TEST_WEBHOOK_KEK_V2
from tests.integration.conftest import drop_tenant_schema, provision_tenant_schema

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]

_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1", "db"})
SECRETO = "secreto-heredado-en-claro-xyz"


class _Audit:
    CANAL_PETICION = "api"  # el router lo lee del módulo real (AREA-03-07)

    def __init__(self) -> None:
        self.asientos: list[dict[str, Any]] = []

    async def append(self, conn: asyncpg.Connection, *, canal: str, **kw: Any) -> str:
        self.asientos.append({"canal": canal, **kw})
        return "hash"


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[tuple[asyncpg.Connection, str, str], None]:
    """(conexión, schema, slug) con la cadena de tenant real (ADR-053)."""
    raw = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    assert raw, "RUN_INTEGRATION=1 sin DATABASE_URL"
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://")
    assert (urlsplit(dsn).hostname or "") in _HOSTS
    c = await asyncpg.connect(dsn)
    sufijo = uuid.uuid4().hex[:8]
    schema = f"tenant_itest_{sufijo}"
    try:
        await provision_tenant_schema(c, schema)
        await c.execute(f'SET search_path TO "{schema}"')
        yield c, schema, f"itest-{sufijo}"
    finally:
        await drop_tenant_schema(c, schema)
        await c.close()


async def _legacy(c: asyncpg.Connection, secret: str | None = SECRETO) -> uuid.UUID:
    return await c.fetchval(
        "INSERT INTO webhook_subscription (url, secret) VALUES ('https://hook.example.com/p', $1) "
        "RETURNING id",
        secret,
    )


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> _Audit:
    a = _Audit()
    monkeypatch.setattr(cifrar, "_audit", a)
    monkeypatch.setattr(cifrar, "_AUDIT_AVAILABLE", True)
    monkeypatch.setattr(rewrap, "_audit", a)
    return a


@pytest.fixture
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "notification.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


async def _entregar(c: asyncpg.Connection, slug: str) -> list[httpx.Request]:
    subs = await WebhookRepository(c).active_for_event("document.radicado.created")
    vistas: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_h)) as cli:
        for s in subs:
            await webhook_service._deliver_one(cli, s, b'{"e":1}', slug)
    return vistas


def _firma(secreto: str) -> str:
    return "sha256=" + hmac.new(secreto.encode(), b'{"e":1}', hashlib.sha256).hexdigest()


# ── migración ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_la_base_rechaza_escribir_un_secreto_nuevo_en_claro(ctx) -> None:
    c, _, _ = ctx
    with pytest.raises(asyncpg.CheckViolationError):
        await _legacy(c)
    # Tampoco mediante UPDATE.
    wid = await _legacy(c, None)
    with pytest.raises(asyncpg.CheckViolationError):
        await c.execute("UPDATE webhook_subscription SET secret='x' WHERE id=$1", wid)


@pytest.mark.asyncio
async def test_el_cifrado_incompleto_se_rechaza(ctx) -> None:
    c, _, _ = ctx
    with pytest.raises(asyncpg.CheckViolationError):
        await c.execute(
            "INSERT INTO webhook_subscription (url, secret_cifrado) VALUES ('https://x.example/', '\\x01')"
        )


# ── crear por el router: cifrado de verdad en la base ──────────────────────
@pytest.mark.asyncio
async def test_crear_por_el_router_no_deja_el_claro_en_ninguna_columna(ctx, monkeypatch, _dns) -> None:
    c, _, slug = ctx
    monkeypatch.setattr(router, "_audit", _Audit())
    resp = await router.create_webhook(
        WebhookCreate(url="https://ext.example.com/h", secret=SECRETO), uuid.uuid4(), slug, c
    )
    assert SECRETO not in resp.model_dump_json()
    fila = await c.fetchrow("SELECT * FROM webhook_subscription WHERE id=$1", resp.id)
    assert fila["secret"] is None
    assert SECRETO not in "".join(str(v) for v in dict(fila).values())
    assert bytes(fila["secret_cifrado"]) != SECRETO.encode()
    # La entrega sale firmada con el secreto ORIGINAL (el receptor lo verifica).
    # `ext.example.com/h` no es el host del mock: se entrega con el DNS parcheado.
    vistas = await _entregar(c, slug)
    assert vistas and vistas[0].headers["X-OrpycaMCP-Signature"] == _firma(SECRETO)


@pytest.mark.asyncio
async def test_listar_no_devuelve_el_secreto_con_filas_reales(ctx, monkeypatch) -> None:
    c, _, slug = ctx
    monkeypatch.setattr(router, "_audit", _Audit())
    await router.create_webhook(
        WebhookCreate(url="https://ext.example.com/h", secret=SECRETO), uuid.uuid4(), slug, c
    )
    class _R:
        headers: dict[str, str] = {}
    out = await router.list_webhooks(_R(), c)  # type: ignore[arg-type]
    assert SECRETO not in "".join(o.model_dump_json() for o in out)
    assert all("secret" not in o.model_dump() for o in out)


# ── cifrado de los heredados ───────────────────────────────────────────────
# ── re-wrap ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rewrap_a_la_kek_nueva_conserva_el_secreto_y_la_firma(ctx, audit, monkeypatch, _dns) -> None:
    c, schema, slug = ctx
    monkeypatch.setattr(router, "_audit", _Audit())
    resp = await router.create_webhook(
        WebhookCreate(url="https://ext.example.com/h", secret=SECRETO), uuid.uuid4(), slug, c
    )
    antes = await c.fetchrow("SELECT secret_cifrado, secret_nonce FROM webhook_subscription")

    # Rotación: v2 al llavero y activa.
    settings.webhook_secret_keks = {2: TEST_WEBHOOK_KEK_V2}
    settings.webhook_secret_kek_active_version = 2
    assert await rewrap.rewrap_tenant(c, schema=schema, dry_run=True) == (1, 0)
    assert await rewrap.rewrap_tenant(c, schema=schema) == (1, 0)
    despues = await c.fetchrow(
        "SELECT secret_cifrado, secret_nonce, secret_kek_version FROM webhook_subscription"
    )
    assert despues["secret_kek_version"] == 2
    assert bytes(despues["secret_nonce"]) != bytes(antes["secret_nonce"])  # nonce NUEVO
    assert bytes(despues["secret_cifrado"]) != bytes(antes["secret_cifrado"])
    a = audit.asientos[-1]
    assert a["action"] == "notification.webhook.secret_rewrapped"
    assert a["payload"] == {"from_version": 1, "to_version": 2} and SECRETO not in str(a)
    # Idempotente.
    assert await rewrap.rewrap_tenant(c, schema=schema) == (0, 0)

    # Retirar la KEK v1 (el campo legacy pasa a otra cosa): la v2 basta y firma igual.
    settings.webhook_secret_kek = "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM="
    vistas = await _entregar(c, slug)
    assert len(vistas) == 1 and vistas[0].headers["X-OrpycaMCP-Signature"] == _firma(SECRETO)
    assert resp.id is not None


@pytest.mark.asyncio
async def test_rewrap_sin_la_kek_vieja_falla_esa_fila_y_no_la_pierde(ctx, audit, monkeypatch) -> None:
    c, schema, slug = ctx
    monkeypatch.setattr(router, "_audit", _Audit())
    await router.create_webhook(
        WebhookCreate(url="https://ext.example.com/h", secret=SECRETO), uuid.uuid4(), slug, c
    )
    antes = bytes(await c.fetchval("SELECT secret_cifrado FROM webhook_subscription"))
    settings.webhook_secret_kek = "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM="  # v1 distinta
    settings.webhook_secret_keks = {2: TEST_WEBHOOK_KEK_V2}
    settings.webhook_secret_kek_active_version = 2
    assert await rewrap.rewrap_tenant(c, schema=schema) == (0, 1)
    assert bytes(await c.fetchval("SELECT secret_cifrado FROM webhook_subscription")) == antes


@pytest.mark.asyncio
async def test_el_check_nace_validado_y_cifrar_no_encuentra_nada(ctx, audit) -> None:
    c, schema, _ = ctx
    q = (
        "SELECT convalidated FROM pg_constraint WHERE conname='chk_webhook_secret_sin_claro' "
        "AND conrelid='webhook_subscription'::regclass"
    )
    assert await c.fetchval(q)
    assert await cifrar.cifrar_tenant(c, schema=schema) == (0, 0)
