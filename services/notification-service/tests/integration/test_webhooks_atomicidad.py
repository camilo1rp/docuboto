# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

"""OMI-03 contra Postgres REAL: el webhook y su asiento commitean o revierten JUNTOS.

Con un mock de `conn.transaction()` solo se prueba que se LLAMA; si el asiento falla,
que la fila de `webhook_subscription` desaparezca solo lo prueba la base.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from app.routers import webhooks as router
from app.schemas.webhook import WebhookCreate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]

_MIGRACIONES = Path(__file__).resolve().parents[2] / "migrations" / "tenant"
_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1", "db"})


class _AuditRoto:
    def __init__(self, falla: bool) -> None:
        self.falla = falla
        self.llamadas = 0

    async def append(self, conn: asyncpg.Connection, **kw: object) -> str:
        self.llamadas += 1
        if self.falla:
            raise RuntimeError("audit_log caído")
        return "hash"


@pytest_asyncio.fixture
async def conn() -> AsyncGenerator[asyncpg.Connection, None]:
    raw = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    assert raw, "RUN_INTEGRATION=1 sin DATABASE_URL"
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://")
    assert (urlsplit(dsn).hostname or "") in _HOSTS
    c = await asyncpg.connect(dsn)
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    await c.execute(f'CREATE SCHEMA "{schema}"')
    try:
        await c.execute(f'SET search_path TO "{schema}", public')
        await c.execute((_MIGRACIONES / "003_webhooks.sql").read_text())
        await c.execute((_MIGRACIONES / "009_webhook_secret_cifrado.sql").read_text())
        yield c
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


async def _filas(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT count(*) FROM webhook_subscription")


@pytest.mark.asyncio
async def test_crear_se_revierte_si_el_asiento_falla(conn, monkeypatch) -> None:
    audit = _AuditRoto(falla=True)
    monkeypatch.setattr(router, "_audit", audit)
    with pytest.raises(RuntimeError):
        await router.create_webhook(
            WebhookCreate(url="https://ext.example.com/h"), uuid.uuid4(), "demo", conn
        )
    assert audit.llamadas == 1, "el asiento no se intentó: la prueba no mide la atomicidad"
    assert await _filas(conn) == 0

    # Control positivo: con la auditoría sana, la fila queda.
    monkeypatch.setattr(router, "_audit", _AuditRoto(falla=False))
    await router.create_webhook(
        WebhookCreate(url="https://ext.example.com/h"), uuid.uuid4(), "demo", conn
    )
    assert await _filas(conn) == 1


@pytest.mark.asyncio
async def test_borrar_se_revierte_si_el_asiento_falla(conn, monkeypatch) -> None:
    wid = await conn.fetchval(
        "INSERT INTO webhook_subscription (url) VALUES ('https://ext.example.com/h') RETURNING id"
    )
    audit = _AuditRoto(falla=True)
    monkeypatch.setattr(router, "_audit", audit)
    with pytest.raises(RuntimeError):
        await router.delete_webhook(wid, uuid.uuid4(), "demo", conn)
    assert audit.llamadas == 1
    assert await _filas(conn) == 1, "el DELETE sobrevivió al fallo del asiento"

    monkeypatch.setattr(router, "_audit", _AuditRoto(falla=False))
    await router.delete_webhook(wid, uuid.uuid4(), "demo", conn)
    assert await _filas(conn) == 0
