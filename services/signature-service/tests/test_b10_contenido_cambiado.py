# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B10 ítem 2: `documento.contenido_cambiado` invalida lo que no recae sobre la huella vigente.

Unitarias (doble de conexión). La atomicidad y el predicado contra Postgres real
están en ``tests/integration/test_b10_invalidacion_sql.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.core.http_clients import BorradorDesaparecidoError, HuellaNoVerificableError
from app.services import invalidacion
from app.workers import document_consumer

_TOKEN = "itest-internal-token-b10-contenido"
_RUTA = "/api/v1/signature/internal/circuitos/borrador"
H1 = "1" * 64


@pytest.fixture
def envelope(monkeypatch: pytest.MonkeyPatch):
    """`parse_fields` doblado: la prueba fija el sobre sin depender del formato Redis."""
    caja: dict = {}
    monkeypatch.setattr(document_consumer.events, "parse_fields", lambda _d: caja["env"])
    return caja


@pytest.fixture
def conn_falsa(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(document_consumer, "acquire_tenant", lambda _s: _Ctx())
    return conn


@pytest.mark.asyncio
async def test_documento_inalcanzable_no_hace_ack(envelope, conn_falsa, monkeypatch) -> None:
    """`HuellaNoVerificableError` sale de `_handle`: sin ack, el mensaje vuelve al PEL."""
    envelope["env"] = {
        "event_id": "e1", "event_type": "documento.contenido_cambiado",
        "tenant_slug": "demo", "payload": {"borrador_id": "b-1"},
    }
    monkeypatch.setattr(
        invalidacion, "get_huella_borrador",
        AsyncMock(side_effect=HuellaNoVerificableError("document caído")),
    )
    with pytest.raises(HuellaNoVerificableError):
        await document_consumer._handle({})


@pytest.mark.asyncio
async def test_evento_sin_borrador_id_se_descarta(envelope, conn_falsa, monkeypatch) -> None:
    envelope["env"] = {
        "event_id": "e1", "event_type": "documento.contenido_cambiado",
        "tenant_slug": "demo", "payload": {},
    }
    huella = AsyncMock()
    monkeypatch.setattr(invalidacion, "get_huella_borrador", huella)
    await document_consumer._handle({})
    huella.assert_not_awaited()


@pytest.mark.asyncio
async def test_otro_tipo_de_evento_se_ignora(envelope, conn_falsa, monkeypatch) -> None:
    envelope["env"] = {
        "event_id": "e1", "event_type": "document.radicado.created",
        "tenant_slug": "demo", "payload": {"borrador_id": "b-1"},
    }
    huella = AsyncMock()
    monkeypatch.setattr(invalidacion, "get_huella_borrador", huella)
    await document_consumer._handle({})
    huella.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_evento_pregunta_la_huella_vigente_y_la_usa_de_predicado(
    envelope, conn_falsa, monkeypatch
) -> None:
    """El evento es solo el disparador: el predicado es la huella de AHORA."""
    envelope["env"] = {
        "event_id": "e9", "event_type": "documento.contenido_cambiado",
        "tenant_slug": "demo",
        "payload": {"borrador_id": "b-1", "cuerpo_sha256": "f" * 64,
                    "cuerpo_sha256_anterior": "e" * 64},
    }
    monkeypatch.setattr(
        invalidacion, "get_huella_borrador", AsyncMock(return_value={"cuerpo_sha256": H1})
    )
    llamado = AsyncMock(return_value=0)
    monkeypatch.setattr(invalidacion, "invalidar_por_contenido", llamado)
    await document_consumer._handle({})
    kw = llamado.await_args.kwargs
    assert kw["vigente"] == H1  # ni el 'nuevo' ni el 'anterior' del evento
    assert kw["origen"] == "evento" and kw["event_id"] == "e9"


@pytest.mark.asyncio
async def test_borrador_desaparecido_resuelve_vigente_none(monkeypatch) -> None:
    monkeypatch.setattr(
        invalidacion, "get_huella_borrador",
        AsyncMock(side_effect=BorradorDesaparecidoError("no está")),
    )
    assert await invalidacion.resolver_huella_vigente("b-1", "demo") is None


@pytest.mark.asyncio
async def test_huella_ilegible_no_se_toma_por_vigente(monkeypatch) -> None:
    monkeypatch.setattr(
        invalidacion, "get_huella_borrador", AsyncMock(return_value={"cuerpo_sha256": None})
    )
    with pytest.raises(HuellaNoVerificableError):
        await invalidacion.resolver_huella_vigente("b-1", "demo")


@pytest.mark.asyncio
async def test_sincrono_exige_vigente(client_with_conn, monkeypatch) -> None:
    """Sin cuerpo (o con una huella mal formada) el endpoint responde 422."""
    client, _conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    h = {"X-Tenant-Slug": "demo", "X-Internal-Token": _TOKEN}

    sin_cuerpo = await client.post(f"{_RUTA}/b-1/invalidar", headers=h)
    mala = await client.post(
        f"{_RUTA}/b-1/invalidar", headers=h, json={"cuerpo_sha256_vigente": "xyz"}
    )
    assert sin_cuerpo.status_code == 422
    assert mala.status_code == 422


@pytest.mark.asyncio
async def test_sincrono_usa_el_mismo_nucleo(client_with_conn, monkeypatch) -> None:
    client, _conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", _TOKEN)
    llamado = AsyncMock(return_value=3)
    monkeypatch.setattr(invalidacion, "invalidar_por_contenido", llamado)

    resp = await client.post(
        f"{_RUTA}/b-1/invalidar",
        headers={"X-Tenant-Slug": "demo", "X-Internal-Token": _TOKEN},
        json={"cuerpo_sha256_vigente": H1},
    )
    assert resp.status_code == 200 and resp.json()["invalidadas"] == 3
    kw = llamado.await_args.kwargs
    assert kw["vigente"] == H1 and kw["origen"] == "sincrono" and kw["objeto_id"] == "b-1"
