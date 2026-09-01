# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Huellas de un lote de ficheros — `POST /storage/internal/files/hashes` (E15 T-08).

La mitad que faltaba para que el índice electrónico lleve la huella de cada
documento (RT-15 art. 4.3.2.3): storage siempre la calculó al subir el anexo, y
no había forma de pedírsela. Quien pregunta es archive-service componiendo el
índice, nunca una persona — de ahí el sellado servicio-a-servicio.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS

_A, _B = uuid.uuid4(), uuid.uuid4()
_HASH = "a" * 64


@pytest.mark.asyncio
async def test_devuelve_la_huella_de_cada_fichero(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [{"id": _A, "sha256": _HASH}]

    resp = await client.post(
        "/api/v1/storage/internal/files/hashes",
        json={"file_ids": [str(_A)]},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == {"hashes": {str(_A): _HASH}}


@pytest.mark.asyncio
async def test_un_id_sin_huella_se_OMITE_en_vez_de_devolver_nada_falso(
    client_with_conn,
) -> None:
    """Un id ausente del resultado significa «no hay huella que dar» —borrado,
    inexistente o sin `sha256`—. Devolver una cadena vacía o un cero haría que
    el índice afirmara haber verificado algo."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [{"id": _A, "sha256": _HASH}, {"id": _B, "sha256": None}]

    resp = await client.post(
        "/api/v1/storage/internal/files/hashes",
        json={"file_ids": [str(_A), str(_B)]},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["hashes"] == {str(_A): _HASH}


@pytest.mark.asyncio
async def test_sin_token_interno_no_se_alcanza(client_with_conn) -> None:
    """La huella de un anexo dice qué documentos custodia un expediente, que
    puede ser clasificado. Como sus vecinos `/download`, `/content` y la
    metadata, este endpoint no tiene llamante externo y va sellado."""
    client, mock_conn, _ = client_with_conn

    resp = await client.post(
        "/api/v1/storage/internal/files/hashes",
        json={"file_ids": [str(_A)]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code in (401, 403)
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_un_lote_vacio_se_rechaza(client_with_conn) -> None:
    """No hay pregunta que hacer, y un `ANY('{}')` recorrería la tabla para
    devolver nada."""
    client, _, _ = client_with_conn
    resp = await client.post(
        "/api/v1/storage/internal/files/hashes",
        json={"file_ids": []},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 422
