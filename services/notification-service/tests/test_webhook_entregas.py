# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Constancia de entregas de webhooks (B4, ADR-018): `envio` ANTES del POST, `resultado` después.

Mutaciones que deben caer: escribir `envio` DESPUÉS del POST (cae el orden); tragar la
excepción de `envio` (cae el fail-closed: el POST saldría sin constancia); guardar el
cuerpo (cae la aserción de que solo viaja el sha256).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import webhook_service
from app.services.webhook_service import BitacoraEntregas, _error_sin_url, _origen_de
from tests.conftest import USER

CUERPO = b'{"payload":{"asunto":"Tutela de Juan Perez"}}'
SUB = {"id": uuid.uuid4(), "url": "https://hook.example.com/T0KEN/secreto?x=1", "secret": None}


@pytest.fixture(autouse=True)
def _dns(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


class _Bitacora:
    """Doble de la constancia: anota el ORDEN de lo que ocurre (envio / POST / resultado)."""

    def __init__(self, eventos: list[str], falla_envio: bool = False) -> None:
        self.eventos = eventos
        self.filas: list[tuple[str, dict]] = []
        self.falla_envio = falla_envio

    async def envio(self, sub, **kw) -> None:
        if self.falla_envio:
            raise RuntimeError("base caída")
        self.eventos.append("envio")
        self.filas.append(("envio", kw))

    async def resultado(self, sub, **kw) -> None:
        self.eventos.append("resultado")
        self.filas.append(("resultado", kw))


async def _entregar(handler, bitacora, sub=SUB) -> bool:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        return await webhook_service._deliver_one(c, sub, CUERPO, "demo", bitacora=bitacora)


@pytest.mark.asyncio
async def test_la_fila_envio_existe_cuando_el_destino_recibe_el_post() -> None:
    eventos: list[str] = []
    bit = _Bitacora(eventos)

    def _h(req: httpx.Request) -> httpx.Response:
        eventos.append("POST")
        return httpx.Response(200)

    assert await _entregar(_h, bit)
    assert eventos == ["envio", "POST", "resultado"], "la constancia previa va ANTES del POST"
    assert bit.filas[1][1]["resultado"] == "entregado" and bit.filas[1][1]["http_status"] == 200
    assert bit.filas[0][1]["entrega_id"] == bit.filas[1][1]["entrega_id"]


@pytest.mark.asyncio
async def test_si_no_se_puede_escribir_la_constancia_previa_no_hay_post() -> None:
    eventos: list[str] = []

    def _h(req: httpx.Request) -> httpx.Response:
        eventos.append("POST")
        return httpx.Response(200)

    ok = await _entregar(_h, _Bitacora(eventos, falla_envio=True))
    assert not ok and eventos == [], "fail-closed: sin constancia previa nada sale"


@pytest.mark.asyncio
async def test_un_http_error_deja_un_resultado_por_intento() -> None:
    eventos: list[str] = []
    bit = _Bitacora(eventos)
    ok = await _entregar(lambda r: httpx.Response(503), bit)
    assert not ok
    assert eventos == ["envio", "resultado"] * 3, "3 intentos, cada uno con su par"
    assert {f[1]["resultado"] for f in bit.filas if f[0] == "resultado"} == {"http_error"}
    assert [f[1]["intento"] for f in bit.filas if f[0] == "envio"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_un_error_de_red_se_registra_sin_la_url() -> None:
    bit = _Bitacora([])

    def _h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no conecta a {req.url}")

    assert not await _entregar(_h, bit)
    errores = [f[1]["error"] for f in bit.filas if f[0] == "resultado"]
    assert errores and all("T0KEN" not in e and "93.184.216.34" not in e and "<url>" in e for e in errores)
    assert {f[1]["resultado"] for f in bit.filas if f[0] == "resultado"} == {"error_red"}


@pytest.mark.asyncio
async def test_destino_rechazado_deja_solo_la_fila_resultado(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))],
    )
    bit = _Bitacora([])
    posts: list[int] = []
    assert not await _entregar(lambda r: (posts.append(1), httpx.Response(200))[1], bit)
    assert posts == [] and [f[0] for f in bit.filas] == ["resultado"]
    assert bit.filas[0][1]["resultado"] == "destino_rechazado"


@pytest.mark.asyncio
async def test_secreto_inutilizable_deja_solo_la_fila_resultado() -> None:
    bit = _Bitacora([])
    sub = {**SUB, "secret_cifrado": None, "secret_en_claro": True}
    assert not await _entregar(lambda r: httpx.Response(200), bit, sub)
    assert [f[0] for f in bit.filas] == ["resultado"] and bit.filas[0][1]["resultado"] == "secreto_inutilizable"


def test_origen_y_error_no_filtran_tokens() -> None:
    assert _origen_de(SUB["url"]) == "https://hook.example.com"
    assert _origen_de("https://u:p@h.example.com:8443/x?y=1") == "https://h.example.com:8443"
    assert "secreto" not in _error_sin_url(RuntimeError("fallo en https://h.example.com/secreto/abc de red"))


@pytest.mark.asyncio
async def test_la_bitacora_real_solo_guarda_el_sha256_y_el_origen(monkeypatch) -> None:
    """Control positivo (escribe algo) + ausencia (ni el cuerpo ni la ruta con el token)."""
    conn = AsyncMock()

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    bit = BitacoraEntregas("demo", event_id="ev-1", event_type="document.radicado.created", recortado=True)
    sha = hashlib.sha256(CUERPO).hexdigest()
    await bit.envio(SUB, entrega_id=uuid.uuid4(), intento=1, body_sha256=sha, firmado=True)
    assert conn.execute.await_count == 1, "debe haber escrito (control positivo)"
    sql, *args = conn.execute.await_args.args
    assert "INSERT INTO webhook_entregas" in sql
    plano = " ".join(str(a) for a in args)
    assert sha in plano and "https://hook.example.com" in args
    assert "Tutela" not in plano and "T0KEN" not in plano and "x=1" not in plano


@pytest.mark.asyncio
async def test_dispatch_cablea_la_bitacora(monkeypatch) -> None:
    capturado: dict = {}

    async def _falso(client, sub, body, tenant_slug="", *, bitacora=None):
        capturado["bitacora"] = bitacora
        return True

    class _Acq:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    monkeypatch.setattr(
        webhook_service.WebhookRepository, "active_for_event", AsyncMock(return_value=[SUB])
    )
    monkeypatch.setattr(webhook_service, "_deliver_one", _falso)
    await webhook_service.dispatch(
        {"tenant_slug": "demo", "event_id": "ev-9", "event_type": "x.y", "payload": {"a": 1}}
    )
    assert isinstance(capturado["bitacora"], BitacoraEntregas), "dispatch SIEMPRE entrega con constancia"


# ── lectura ─────────────────────────────────────────────────────────────────
def _fila(**kw) -> dict:
    base = {
        "id": uuid.uuid4(), "entrega_id": uuid.uuid4(), "fase": "resultado",
        "webhook_id": uuid.uuid4(), "url_destino": "https://hook.example.com", "url_sha256": "a" * 64,
        "event_id": "ev-1", "event_type": "x.y", "intento": 1, "body_sha256": "b" * 64,
        "recortado": False, "firmado": True, "resultado": "entregado", "http_status": 200,
        "error": None, "at": datetime.now(tz=UTC),
    }
    return {**base, **kw}


@pytest.mark.asyncio
async def test_get_entregas_pagina_y_cuenta(client, db_conn) -> None:
    db_conn.fetch.return_value = [_fila(), _fila(fase="envio", resultado=None, http_status=None)]
    db_conn.fetchval.return_value = 1  # el gate responde True a la consulta de permiso; el conteo lo ve 1 también
    wid = uuid.uuid4()
    r = await client.get(
        f"/api/v1/webhooks/{wid}/entregas?page=2&size=5",
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2 and "x-total-count" in r.headers
    args = db_conn.fetch.await_args.args
    assert args[1] == wid and args[2] == 5 and args[3] == 5, "LIMIT size OFFSET (page-1)*size"
    assert "cuerpo" not in r.text and "body" not in set(r.json()[0])


@pytest.mark.asyncio
async def test_get_entregas_exige_admin(client, db_conn) -> None:
    db_conn.fetchval.return_value = False  # check_permission niega
    r = await client.get(
        f"/api/v1/webhooks/{uuid.uuid4()}/entregas", headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)}
    )
    assert r.status_code == 403
    db_conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_url_sha256_es_la_huella_del_origen_no_del_url_completo(monkeypatch) -> None:
    """B4 baja: un hash sin sal del URL completo confirmaría un token de Slack/Teams
    adivinado. Solo se hashea scheme://host:port."""
    import hashlib

    class _Acq:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    insertar = AsyncMock()
    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    monkeypatch.setattr(webhook_service.WebhookEntregasRepository, "__init__", lambda self, conn: None)
    monkeypatch.setattr(webhook_service.WebhookEntregasRepository, "insert", insertar)
    bit = BitacoraEntregas("demo", event_id="ev", event_type="x.y", recortado=False)
    await bit.envio(SUB, entrega_id=uuid.uuid4(), intento=1, body_sha256="b" * 64, firmado=False)
    huella = insertar.await_args.kwargs["url_sha256"]
    assert huella == hashlib.sha256(b"https://hook.example.com").hexdigest()
    assert huella != hashlib.sha256(SUB["url"].encode()).hexdigest()
