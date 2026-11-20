# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`document.api.radicado_fingerprint` (AREA-04-09 Media-2).

signature fija esta huella —SHA-256 del documento principal VIGENTE del
radicado, tal como la CALCULÓ el almacén al subirlo— al abrir una cadena de
firma y la relee antes de cada firma. Aquí: la forma del resultado y el
fail-closed. El SQL se prueba contra Postgres real en
`tests/document/integration/test_internal_huella_radicado_integration.py`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from document import api as document_api
from storage import api as storage_api
from tests.document.conftest import TENANT_SLUG

REF = "2024-DEMO-E-000001"
HUELLA = "AB" * 32
RAD = {"id": uuid.uuid4(), "tracking_number": REF}
ANEXO = {"id": uuid.uuid4(), "file_id": uuid.uuid4(), "filename": "oficio.pdf"}


def _storage(monkeypatch, *, huella=HUELLA, error=None) -> AsyncMock:
    mock = AsyncMock(return_value=huella, side_effect=error)
    monkeypatch.setattr(storage_api, "get_file_sha256", mock)
    return mock


async def _huella():
    return await document_api.radicado_fingerprint(tenant_slug=TENANT_SLUG, ref=REF)


@pytest.mark.asyncio
async def test_devuelve_la_huella_de_storage_en_minuscula(api_conn, monkeypatch) -> None:
    api_conn.fetchrow.side_effect = [RAD, ANEXO]
    storage = _storage(monkeypatch)
    body = await _huella()
    assert body["huella_sha256"] == HUELLA.lower()
    assert body["tracking_number"] == REF
    assert body["file_id"] == str(ANEXO["file_id"])
    # La huella la pone el ALMACÉN (la calculó al subir), no una columna de document.
    storage.assert_awaited_once()
    assert storage.await_args.kwargs["file_id"] == ANEXO["file_id"]
    assert storage.await_args.kwargs["tenant_slug"] == TENANT_SLUG
    # La referencia viaja como parámetro, jamás interpolada en el SQL.
    for llamada in api_conn.fetchrow.await_args_list:
        assert REF not in llamada.args[0]


@pytest.mark.asyncio
async def test_radicado_inexistente(api_conn, monkeypatch) -> None:
    api_conn.fetchrow.side_effect = [None]
    _storage(monkeypatch, error=RuntimeError("no debe llamarse"))
    with pytest.raises(document_api.DocumentNotFoundError):
        await _huella()


@pytest.mark.asyncio
async def test_radicado_sin_documento_principal(api_conn, monkeypatch) -> None:
    api_conn.fetchrow.side_effect = [RAD, None]
    _storage(monkeypatch, error=RuntimeError("no debe llamarse"))
    with pytest.raises(document_api.NothingToSealError) as exc:
        await _huella()
    assert exc.value.code == "radicado_sin_documento"


@pytest.mark.asyncio
async def test_storage_no_conoce_el_fichero(api_conn, monkeypatch) -> None:
    api_conn.fetchrow.side_effect = [RAD, ANEXO]
    _storage(monkeypatch, huella=None)
    with pytest.raises(document_api.NothingToSealError) as exc:
        await _huella()
    assert exc.value.code == "anexo_sin_huella"


@pytest.mark.asyncio
async def test_storage_caido_nunca_una_huella_inventada(api_conn, monkeypatch) -> None:
    api_conn.fetchrow.side_effect = [RAD, ANEXO]
    _storage(monkeypatch, error=storage_api.StorageError("HTTP 500: b'SECRETO db-7:5432'"))
    with pytest.raises(document_api.DocumentUnavailableError) as exc:
        await _huella()
    # El detalle del colaborador no viaja a quien consulta.
    assert "SECRETO" not in str(exc.value)
