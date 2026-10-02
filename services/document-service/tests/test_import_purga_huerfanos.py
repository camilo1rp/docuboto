# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Contrato saliente y cableado de la purga de binarios huérfanos (AREA-09-14).

Las pruebas con Postgres real (`tests/integration/test_import_huerfanos_integration.py`)
cubren la cola y los asientos; aquí lo que solo se ve desde fuera: qué viaja a storage
(la petición lleva el token interno, y el tenant) y que la purga se dispara también
cuando un ítem se omite por carrera tras CONFIRMAR la transacción.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import asyncpg
import httpx
import pytest

from app.clients import storage_client
from tests.test_import import _pkg, _svc


def _transporte(monkeypatch, handler) -> None:
    real = httpx.AsyncClient

    def _factory(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(storage_client.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(storage_client.settings, "storage_service_url", "http://storage")
    monkeypatch.setattr(storage_client.settings, "storage_internal_token", "token-interno-de-prueba")


@pytest.mark.asyncio
async def test_la_peticion_de_purga_lleva_el_token_interno_el_tenant_y_el_cuerpo(monkeypatch) -> None:
    """Mutación: quitar `X-Internal-Token` → storage responde 401 y nada se purga nunca."""
    capturadas: list[httpx.Request] = []
    fid, actor = uuid.uuid4(), uuid.uuid4()
    desde = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)

    def _h(req: httpx.Request) -> httpx.Response:
        capturadas.append(req)
        return httpx.Response(
            200, json={"resultados": [{"file_id": str(fid), "resultado": "purgado", "detalle": None}]}
        )

    _transporte(monkeypatch, _h)
    out = await storage_client.purgar_huerfanos(
        tenant_slug="demo", file_ids=[fid], referencia="imp-1", motivo="m", subido_desde=desde, actor=actor
    )
    (req,) = capturadas
    assert req.method == "POST" and req.url.path == "/api/v1/storage/internal/purgar-huerfanos"
    assert req.headers["X-Internal-Token"] == "token-interno-de-prueba"
    assert req.headers["X-Tenant-Slug"] == "demo"
    import json

    cuerpo = json.loads(req.content)
    assert cuerpo["file_ids"] == [str(fid)] and cuerpo["referencia"] == "imp-1"
    assert cuerpo["actor"] == str(actor) and cuerpo["subido_desde"].startswith("2026-10-02T10:00:00")
    assert [(r.file_id, r.resultado) for r in out] == [(fid, "purgado")]


@pytest.mark.asyncio
@pytest.mark.parametrize("respuesta", [httpx.Response(401), httpx.Response(500, text="x"),
                                       httpx.Response(200, json={"otra": 1})])
async def test_una_respuesta_no_valida_es_error_y_nunca_un_purgado(monkeypatch, respuesta) -> None:
    _transporte(monkeypatch, lambda req: respuesta)
    with pytest.raises(storage_client.StorageContentError):
        await storage_client.purgar_huerfanos(
            tenant_slug="demo", file_ids=[uuid.uuid4()], referencia="i", motivo="m",
            subido_desde=datetime.now(tz=UTC), actor=None,
        )


@pytest.mark.asyncio
async def test_storage_inalcanzable_es_error_recuperable(monkeypatch) -> None:
    def _h(req):
        raise httpx.ConnectError("sin red")

    _transporte(monkeypatch, _h)
    with pytest.raises(storage_client.StorageContentError, match="inalcanzable"):
        await storage_client.purgar_huerfanos(
            tenant_slug="demo", file_ids=[uuid.uuid4()], referencia="i", motivo="m",
            subido_desde=datetime.now(tz=UTC), actor=None,
        )


@pytest.mark.asyncio
async def test_un_item_omitido_por_carrera_dispara_la_purga_tras_confirmar() -> None:
    """Mutación: quitar la llamada posterior a la transacción → esos binarios solo se
    asientan y quedan en el almacén hasta que el barrido pase."""
    svc = _svc()
    svc._repo.insert_radicado.side_effect = asyncpg.UniqueViolationError("dup")
    purga = AsyncMock()
    svc._huerfanos.registrar = AsyncMock()
    svc._huerfanos.purgar = purga
    pkg = _pkg([{"tracking": "2026-OTRA-E-000001", "anexos": [("a.pdf", b"PDF")]}])
    with patch(
        "app.services.import_service.storage_client.upload_bytes",
        new=AsyncMock(return_value=storage_client.ArchivoSubido(file_id=uuid.uuid4(), sha256="x")),
    ), patch("app.services.import_service.audit.append", new=AsyncMock(return_value="h")):
        rep = await svc.import_package(zip_bytes=pkg, max_clearance=3, actor=None)
    assert rep["importados"] == 0
    svc._huerfanos.registrar.assert_awaited_once()
    purga.assert_awaited_once()
    assert purga.await_args.kwargs["import_uuid"] == uuid.UUID(rep["import_uuid"])
