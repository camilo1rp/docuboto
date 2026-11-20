# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`document.api.read_anexo_content` solo sirve un nivel habilitado."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from document import api as document_api
from tests.document.conftest import TENANT_SLUG

ANEXO = uuid.uuid4()
ARCHIVO = uuid.uuid4()


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
async def test_un_anexo_que_no_sale_no_abre_el_almacen(
    api_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = api_conn
    conn.fetchrow.side_effect = _filas(None)

    async def abierto(**kwargs: Any) -> Any:
        raise AssertionError(kwargs)

    monkeypatch.setattr(document_api.storage_api, "open_file_stream", abierto)
    assert (
        await document_api.read_anexo_content(
            tenant_slug=TENANT_SLUG, anexo_id=ANEXO, max_bytes=1024
        )
        is None
    )


@pytest.mark.asyncio
async def test_un_nivel_habilitado_sirve_los_bytes_y_deja_traza(
    api_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = api_conn
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

    monkeypatch.setattr(document_api.storage_api, "open_file_stream", abierto)
    monkeypatch.setattr(document_api.audit, "append", traza)
    contenido = await document_api.read_anexo_content(
        tenant_slug=TENANT_SLUG, anexo_id=ANEXO, max_bytes=1024
    )
    assert contenido == b"hola"
    assert stream.cerrado
    assert asientos[0]["action"] == "document.contenido_transmitido"
    assert asientos[0]["payload"]["detalle"] == "transmitido al proveedor de inteligencia"
    assert asientos[0]["object_ref"] == "2026-E-1"
