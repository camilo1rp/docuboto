# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.


"""`webhook_entregas` contra Postgres REAL (B4, ADR-018): migración 012, append-only y cableado.

Mutaciones: quitar el trigger (cae el UPDATE/DELETE/TRUNCATE); poner FK a la suscripción
(cae «borrar la suscripción no borra la constancia»); escribir `envio` tras el POST (cae
el orden observado desde el handler contra la base).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.repositories.webhook_entregas_repository import WebhookEntregasRepository
from app.services import webhook_service
from app.services.webhook_service import BitacoraEntregas

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]

_MIGRACIONES = Path(__file__).resolve().parents[2] / "migrations" / "tenant"
_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1", "db"})


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
        await c.execute(f'SET search_path TO "{schema}"')
        await c.execute((_MIGRACIONES / "003_webhooks.sql").read_text())
        await c.execute((_MIGRACIONES / "012_webhook_entregas.sql").read_text())
        await c.execute((_MIGRACIONES / "013_webhook_entregas_mensaje.sql").read_text())
        yield c
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


async def _fila(c: asyncpg.Connection, wid: uuid.UUID, fase: str = "envio", **kw) -> None:
    await WebhookEntregasRepository(c).insert(
        entrega_id=uuid.uuid4(), fase=fase, webhook_id=wid, url_destino="https://h.example.com",
        url_sha256="a" * 64, event_id="ev", event_type="x.y", intento=1, body_sha256="b" * 64,
        recortado=False, firmado=False, **kw,
    )


@pytest.mark.asyncio
async def test_migracion_idempotente(conn) -> None:
    for n in ("012_webhook_entregas", "013_webhook_entregas_mensaje"):
        await conn.execute((_MIGRACIONES / f"{n}.sql").read_text())
        await conn.execute((_MIGRACIONES / f"{n}.sql").read_text())


@pytest.mark.asyncio
async def test_el_mensaje_del_trigger_es_traza_tecnica_sin_citar_la_ley(conn) -> None:
    await _fila(conn, uuid.uuid4())
    with pytest.raises(asyncpg.PostgresError) as exc:
        await conn.execute("DELETE FROM webhook_entregas")
    assert "traza tecnica" in str(exc.value) and "594" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("sentencia", [
    "UPDATE webhook_entregas SET error = 'x'",
    "DELETE FROM webhook_entregas",
    "TRUNCATE webhook_entregas",
])
async def test_es_append_only(conn, sentencia: str) -> None:
    await _fila(conn, uuid.uuid4())
    with pytest.raises(asyncpg.PostgresError, match="append-only"):
        await conn.execute(sentencia)
    assert await conn.fetchval("SELECT count(*) FROM webhook_entregas") == 1


@pytest.mark.asyncio
async def test_los_check_de_fase_y_resultado(conn) -> None:
    wid = uuid.uuid4()
    for malo in (
        dict(fase="envio", resultado="entregado"),       # envio no lleva desenlace
        dict(fase="resultado"),                          # resultado exige desenlace
        dict(fase="resultado", resultado="inventado"),   # fuera del catálogo
        dict(fase="otra"),
    ):
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await _fila(conn, wid, **malo)
        await sp.rollback()
    await _fila(conn, wid, "resultado", resultado="http_error", http_status=502)


@pytest.mark.asyncio
async def test_borrar_la_suscripcion_no_borra_la_constancia(conn) -> None:
    sid = await conn.fetchval("INSERT INTO webhook_subscription (url) VALUES ('https://h.example.com') RETURNING id")
    await _fila(conn, sid)
    await conn.execute("DELETE FROM webhook_subscription WHERE id = $1", sid)
    assert await conn.fetchval("SELECT count(*) FROM webhook_entregas WHERE webhook_id = $1", sid) == 1


@pytest.mark.asyncio
async def test_extremo_a_extremo_la_fila_envio_esta_en_la_base_cuando_llega_el_post(conn, monkeypatch) -> None:
    sid = await conn.fetchval("INSERT INTO webhook_subscription (url) VALUES ('https://hook.example.com/p') RETURNING id")

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    vistas: list[int] = []

    async def _handler(req: httpx.Request) -> httpx.Response:
        vistas.append(await conn.fetchval("SELECT count(*) FROM webhook_entregas WHERE fase = 'envio'"))
        return httpx.Response(200)

    sub = {"id": sid, "url": "https://hook.example.com/p", "secret": None}
    bit = BitacoraEntregas("demo", event_id="ev-1", event_type="x.y", recortado=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as cli:
        assert await webhook_service._deliver_one(cli, sub, b'{"a":1}', "demo", bitacora=bit)
    assert vistas == [1], "al llegar el POST ya existía la fila envio"
    filas = await conn.fetch("SELECT fase, resultado, http_status, intento FROM webhook_entregas ORDER BY at, fase DESC")
    assert [(f["fase"], f["resultado"], f["http_status"]) for f in filas] == [("envio", None, None), ("resultado", "entregado", 200)]


@pytest.mark.asyncio
async def test_si_la_tabla_no_existe_no_se_envia(conn, monkeypatch) -> None:
    await conn.execute("DROP TABLE webhook_entregas")

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    posts: list[int] = []
    sub = {"id": uuid.uuid4(), "url": "https://hook.example.com/p", "secret": None}
    bit = BitacoraEntregas("demo", event_id=None, event_type="x.y", recortado=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (posts.append(1), httpx.Response(200))[1])) as cli:
        assert not await webhook_service._deliver_one(cli, sub, b"{}", "demo", bitacora=bit)
    assert posts == []
