# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B4 — el `dedup_key` del evento PREMIS de una fixity fallida lleva el NÚMERO de intento.

Mutación que debe caer: volver al sello de tiempo (dos fallos darían claves que no son `:1`/`:2`)
o no incrementar el contador en la misma tx que el asiento `*_failed`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TENANT_HEADERS, make_expediente_record, make_radicado_link_record
from tests.test_transferencias import _indice_row, _transfer_row


async def _recibir_con_fixity_fallida(client, mock_conn, tid, exp_id, contador: list[int]):
    doc_id = uuid.uuid4()

    async def _fetchval(query, *args):
        if "fixity_fallos_intentos" in query:
            contador[0] += 1
            return contador[0]
        return True

    mock_conn.fetchval.side_effect = _fetchval
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _indice_row(),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _indice_row(),
    ]
    mock_conn.fetch.side_effect = [
        [make_radicado_link_record(expediente_id=exp_id, radicado_id=doc_id, content_hash="a" * 64)],
        [{"document_id": doc_id, "valor_huella": "b" * 64}],
    ]
    return await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)


@pytest.mark.asyncio
async def test_dos_fixity_fallidas_dejan_claves_de_intento_1_y_2(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    contador = [0]
    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=True)
    ) as post:
        for _ in range(2):
            resp = await _recibir_con_fixity_fallida(client, mock_conn, tid, exp_id, contador)
            assert resp.status_code == 409
    claves = [c.kwargs["dedup_key"] for c in post.await_args_list]
    assert claves == [f"fixity-recepcion-fallo:{tid}:1", f"fixity-recepcion-fallo:{tid}:2"]


@pytest.mark.asyncio
async def test_el_contador_y_el_asiento_failed_van_en_la_misma_tx(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    eventos: list[str] = []

    class _Tx:
        async def __aenter__(self) -> None:
            eventos.append("tx_in")

        async def __aexit__(self, *a) -> None:
            eventos.append("tx_out")

    mock_conn.transaction.side_effect = lambda *a, **k: _Tx()
    contador = [0]

    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=True)
    ):
        resp = await _recibir_con_fixity_fallida(client, mock_conn, tid, exp_id, contador)
        assert resp.status_code == 409

    # el último bloque tx_in..tx_out contiene el UPDATE del contador y el asiento con `intento`
    assert contador[0] == 1
    assert eventos.count("tx_in") == eventos.count("tx_out") >= 1
    # el asiento `*_failed` lleva el número de intento que devolvió el UPDATE del contador
    assert any(
        '"intento": 1' in str(c.args) or "'intento': 1" in str(c.args)
        for c in mock_conn.execute.call_args_list
    )


@pytest.mark.asyncio
async def test_un_409_que_no_es_fixity_no_toca_el_contador(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _indice_row(estado="borrador"),
    ]
    resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert not [c for c in mock_conn.fetchval.call_args_list if "fixity_fallos_intentos" in str(c.args)]
