# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Eventos PREMIS por `storage.api`: lectura de varios objetos y registro idempotente
(AREA-07-05, AREA-07-15). El no-read-up lo aplica archive antes de llamar."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from storage import api as storage_api
from tests.storage.conftest import TENANT_SLUG

_NOW = datetime.now(tz=UTC)

Fixture = tuple[AsyncClient, AsyncMock, MagicMock]


def _evento_row(object_ref: str = "EXP-2024-0001") -> dict:
    return {
        "id": uuid.uuid4(),
        "object_ref": object_ref,
        "tipo": "FIJACION",
        "resultado": "ok",
        "hash_before": "a" * 64,
        "hash_after": "a" * 64,
        "detalle": None,
        "agente": "system",
        "ts": _NOW,
    }


@pytest.mark.asyncio
async def test_list_events_sin_eventos_es_lista_vacia(client_with_conn: Fixture) -> None:
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []

    assert await storage_api.list_events(tenant_slug=TENANT_SLUG, object_refs=["EXP-1"]) == []


@pytest.mark.asyncio
async def test_list_events_sin_refs_no_enumera_el_tenant(client_with_conn: Fixture) -> None:
    """Sin objetos no hay nada que pedir: nunca se cae a «todos los eventos»."""
    _client, mock_conn, _ = client_with_conn

    assert await storage_api.list_events(tenant_slug=TENANT_SLUG, object_refs=[]) == []
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_list_events_pide_varios_objetos_en_una_consulta(client_with_conn: Fixture) -> None:
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [_evento_row()]

    eventos = await storage_api.list_events(
        tenant_slug=TENANT_SLUG, object_refs=["EXP-1", "file-uuid", "aip-uuid"]
    )

    sql, refs = mock_conn.fetch.call_args.args
    assert "ANY($1::text[])" in sql
    assert refs == ["EXP-1", "file-uuid", "aip-uuid"]
    assert eventos[0]["tipo"] == "FIJACION" and eventos[0]["detalle"] == {}


@pytest.mark.asyncio
async def test_list_events_un_fallo_es_storage_error(client_with_conn: Fixture) -> None:
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetch.side_effect = RuntimeError("bd caída")

    with pytest.raises(storage_api.StorageError):
        await storage_api.list_events(tenant_slug=TENANT_SLUG, object_refs=["EXP-1"])


_EVENTO = {
    "tenant_slug": TENANT_SLUG,
    "object_ref": "EXP-2024-0001",
    "tipo": "FIJACION",
    "resultado": "ok",
    "agente": "uuid-del-receptor",
    "dedup_key": "fixity-recepcion:t-1",
    "detalle": {"origen": "recepcion_transferencia", "documentos_sin_huella": 2},
}


@pytest.mark.asyncio
async def test_record_event_registra_con_agente_y_clave(client_with_conn: Fixture) -> None:
    _client, mock_conn, _ = client_with_conn
    # 1.º fetchrow: búsqueda por dedup (nada); 2.º: el INSERT ... RETURNING
    mock_conn.fetchrow.side_effect = [None, _evento_row()]

    assert await storage_api.record_event(**_EVENTO) is True

    insert = mock_conn.fetchrow.call_args_list[1]
    assert "INSERT INTO preservacion_evento" in insert.args[0]
    # (sql, object_ref, tipo, resultado, hash_before, hash_after, detalle, agente)
    assert insert.args[1] == "EXP-2024-0001" and insert.args[2] == "FIJACION"
    assert '"dedup_key": "fixity-recepcion:t-1"' in insert.args[6]
    assert insert.args[7] == "uuid-del-receptor"


@pytest.mark.asyncio
async def test_record_event_repetido_no_duplica(client_with_conn: Fixture) -> None:
    """Idempotencia: la reconciliación reintenta (at-least-once) sobre un registro
    append-only."""
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.side_effect = [_evento_row()]  # la búsqueda por dedup SÍ encuentra

    assert await storage_api.record_event(**_EVENTO) is True
    assert mock_conn.fetchrow.await_count == 1
    assert "detalle->>'dedup_key'" in mock_conn.fetchrow.call_args.args[0]


@pytest.mark.asyncio
async def test_record_event_un_fallo_es_false_y_no_lanza(client_with_conn: Fixture) -> None:
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.side_effect = RuntimeError("bd caída")

    assert await storage_api.record_event(**_EVENTO) is False
