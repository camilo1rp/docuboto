# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contratos salientes de B2 (ADR-031): procedencia en storage y lectura estricta en tenant."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.clients import storage_client
from app.clients.tenant_client import TenantServiceClient, TenantServiceError


def _http(monkeypatch, handler) -> list[httpx.Request]:
    capturadas: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _handler(req: httpx.Request) -> httpx.Response:
        capturadas.append(req)
        return handler(req)

    def _factory(*a, **k):
        k["transport"] = httpx.MockTransport(_handler)
        return real(*a, **k)

    monkeypatch.setattr(storage_client.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(storage_client.settings, "storage_internal_token", "tok-interno")
    monkeypatch.setattr(storage_client.settings, "storage_service_url", "http://storage")
    return capturadas


@pytest.mark.asyncio
async def test_procedencia_cumple_el_contrato_de_storage(monkeypatch) -> None:
    fid, autor = uuid.uuid4(), uuid.uuid4()
    reqs = _http(
        monkeypatch,
        lambda r: httpx.Response(
            200,
            json={"sha256": "ab" * 32, "uploaded_by": str(autor), "referencias": ["anexos.file_id"]},
        ),
    )
    p = await storage_client.fetch_file_procedencia(tenant_slug="demo", file_id=fid)
    assert (p.sha256, p.uploaded_by, p.referencias) == ("ab" * 32, autor, ["anexos.file_id"])
    (req,) = reqs
    assert req.url.path == "/api/v1/storage/internal/files/procedencia"
    assert req.headers["X-Internal-Token"] == "tok-interno"
    assert req.headers["X-Tenant-Slug"] == "demo"
    assert json.loads(req.content) == {"file_id": str(fid)}


@pytest.mark.asyncio
async def test_procedencia_404_es_none(monkeypatch) -> None:
    _http(monkeypatch, lambda r: httpx.Response(404, json={}))
    assert await storage_client.fetch_file_procedencia(tenant_slug="d", file_id=uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_el_autor_nulo_se_conserva(monkeypatch) -> None:
    _http(
        monkeypatch,
        lambda r: httpx.Response(200, json={"sha256": "ab" * 32, "uploaded_by": None, "referencias": []}),
    )
    p = await storage_client.fetch_file_procedencia(tenant_slug="d", file_id=uuid.uuid4())
    assert p.uploaded_by is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 500, 503])
async def test_procedencia_http_inesperado_no_se_degrada(monkeypatch, status) -> None:
    _http(monkeypatch, lambda r: httpx.Response(status, json={}))
    with pytest.raises(storage_client.StorageContentError):
        await storage_client.fetch_file_procedencia(tenant_slug="d", file_id=uuid.uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("respuesta", [httpx.Response(500, json={}), httpx.Response(200, json=[1])])
async def test_params_estricto_lanza_donde_get_params_traga(respuesta) -> None:
    http = httpx.AsyncClient(
        base_url="http://tenant", transport=httpx.MockTransport(lambda r: respuesta)
    )
    cliente = TenantServiceClient(http)
    with pytest.raises(TenantServiceError):
        await cliente.get_params_estricto(tenant_slug="demo")
    assert await cliente.get_params(tenant_slug="demo") == {}


@pytest.mark.asyncio
async def test_params_estricto_devuelve_los_parametros() -> None:
    http = httpx.AsyncClient(
        base_url="http://tenant",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"a": "b"})),
    )
    assert await TenantServiceClient(http).get_params_estricto(tenant_slug="demo") == {"a": "b"}
