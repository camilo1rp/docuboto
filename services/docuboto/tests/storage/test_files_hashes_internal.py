# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Huellas de un lote de ficheros por `storage.api` (E15 T-08).

La mitad que faltaba para que el índice electrónico lleve la huella de cada
documento (RT-15 art. 4.3.2.3): storage siempre la calculó al subir el anexo.
Archive la pide en lote (best-effort); la firma declarativa de document, de una en
una y sin degradar.
"""

from __future__ import annotations

import uuid

import pytest

from storage import api as storage_api
from tests.storage.conftest import TENANT_SLUG

_A, _B = uuid.uuid4(), uuid.uuid4()
_HASH = "a" * 64


@pytest.mark.asyncio
async def test_devuelve_la_huella_de_cada_fichero(client_with_conn) -> None:
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [{"id": _A, "sha256": _HASH}]

    hashes = await storage_api.get_file_hashes(tenant_slug=TENANT_SLUG, file_ids=[str(_A)])
    assert hashes == {str(_A): _HASH}


@pytest.mark.asyncio
async def test_un_id_sin_huella_se_OMITE_en_vez_de_devolver_nada_falso(
    client_with_conn,
) -> None:
    """Un id ausente del resultado significa «no hay huella que dar» —borrado,
    inexistente o sin `sha256`—. Devolver una cadena vacía o un cero haría que
    el índice afirmara haber verificado algo."""
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [{"id": _A, "sha256": _HASH}, {"id": _B, "sha256": None}]

    hashes = await storage_api.get_file_hashes(
        tenant_slug=TENANT_SLUG, file_ids=[str(_A), str(_B)]
    )
    assert hashes == {str(_A): _HASH}


@pytest.mark.asyncio
async def test_un_lote_vacio_no_consulta(client_with_conn) -> None:
    """No hay pregunta que hacer, y un `ANY('{}')` recorrería la tabla para
    devolver nada."""
    _client, mock_conn, _ = client_with_conn
    assert await storage_api.get_file_hashes(tenant_slug=TENANT_SLUG, file_ids=[]) == {}
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_el_lote_es_best_effort(client_with_conn) -> None:
    """Vincular no queda preso de esta lectura: un fallo es `{}`, no una excepción."""
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetch.side_effect = RuntimeError("bd caída")
    assert await storage_api.get_file_hashes(tenant_slug=TENANT_SLUG, file_ids=[str(_A)]) == {}


@pytest.mark.asyncio
async def test_la_huella_de_uno_no_se_degrada(client_with_conn) -> None:
    """Firmar sin saber QUÉ se firma es peor que no firmar: el fallo sube."""
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [{"id": _A, "sha256": _HASH}]
    assert await storage_api.get_file_sha256(tenant_slug=TENANT_SLUG, file_id=_A) == _HASH
    assert await storage_api.get_file_sha256(tenant_slug=TENANT_SLUG, file_id=_B) is None

    mock_conn.fetch.side_effect = RuntimeError("bd caída")
    with pytest.raises(storage_api.StorageError):
        await storage_api.get_file_sha256(tenant_slug=TENANT_SLUG, file_id=_A)
