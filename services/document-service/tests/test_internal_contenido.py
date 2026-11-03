# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /internal/documentos/anexos/{id}/contenido` solo sirve un nivel habilitado."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from document.core import config as cfg_module
from document.routers import internal
from tests.conftest import TENANT_SLUG

TOKEN = "test-document-internal-token-xxx"
ANEXO = uuid.uuid4()
ARCHIVO = uuid.uuid4()
RUTA = f"/api/v1/internal/documentos/anexos/{ANEXO}/contenido"


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TOKEN)


class _Stream:
    def __init__(self) -> None:
        self.cerrado = False

    def aiter_bytes(self):
        async def _gen():
            yield b"hola"

        return _gen()

    async def aclose(self) -> None:
        self.cerrado = True


def _filas(fila: dict[str, Any] | None) -> Any:
    async def fetchrow(sql: str, *args: Any) -> Any:
        if "public.tenants" in sql:
            return {"intelligence_levels": [1]}
        if "contenido:anexo" in sql:
            assert "anulado" in sql and "nivel_seguridad = ANY" in sql
            assert args[0] == ANEXO
            assert args[1] == [1]
            return fila
        raise AssertionError(sql)

    return fetchrow


@pytest.mark.asyncio
async def test_sin_token_es_401(client_with_conn) -> None:
    client, _conn = client_with_conn
    resp = await client.get(RUTA, headers={"X-Tenant-Slug": TENANT_SLUG})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_un_anexo_que_no_sale_no_abre_el_almacen(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = _filas(None)

    async def abierto(**kwargs: Any) -> Any:
        raise AssertionError(kwargs)

    monkeypatch.setattr(internal.storage_client, "open_content_stream", abierto)
    resp = await client.get(RUTA, headers={"X-Tenant-Slug": TENANT_SLUG, "X-Internal-Token": TOKEN})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_un_nivel_habilitado_sirve_los_bytes_y_deja_traza(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = _filas(
        {"file_id": ARCHIVO, "tracking_number": "2026-E-1", "nivel_seguridad": 1}
    )
    stream = _Stream()
    asientos: list[dict[str, Any]] = []

    async def abierto(*, tenant_slug: str, file_id: uuid.UUID) -> _Stream:
        assert tenant_slug == TENANT_SLUG
        assert file_id == ARCHIVO
        return stream

    async def traza(*args: Any, **kwargs: Any) -> None:
        asientos.append(kwargs)

    monkeypatch.setattr(internal.storage_client, "open_content_stream", abierto)
    monkeypatch.setattr(internal.audit, "append", traza)
    resp = await client.get(RUTA, headers={"X-Tenant-Slug": TENANT_SLUG, "X-Internal-Token": TOKEN})
    assert resp.status_code == 200
    assert resp.content == b"hola"
    assert asientos[0]["action"] == "document.contenido_transmitido"
    assert asientos[0]["payload"]["detalle"] == "transmitido al proveedor de inteligencia"
    assert asientos[0]["object_ref"] == "2026-E-1"
