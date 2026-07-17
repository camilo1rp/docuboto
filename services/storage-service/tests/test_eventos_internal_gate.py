# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate D-02 (X-Internal-Token) sobre `GET /preservacion/eventos`
(continuación de E10 Increment C, cierre del no-read-up de los eventos PREMIS
mediados por archive-service, RF-SEG-08). El permiso `USUA_PERM_EXPEDIENTE`
YA NO gatea este endpoint — storage-service no conoce el `nivel_seguridad`
del expediente, así que el no-read-up real vive en archive-service; este
endpoint pasa a ser puramente interno, mismo patrón que
`test_aip_internal_gate.py` para `GET /preservacion/aip/{expediente_id}`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.core import config as cfg_module
from tests.conftest import TENANT_HEADERS, TEST_INTERNAL_TOKEN

_NOW = datetime.now(tz=UTC)
_PATH = "/api/v1/preservacion/eventos?object_ref=EXP-2024-0001"


def _evento_row() -> dict:
    return {
        "id": uuid.uuid4(),
        "object_ref": "EXP-2024-0001",
        "tipo": "FIJACION",
        "resultado": "ok",
        "hash_before": "a" * 64,
        "hash_after": "a" * 64,
        "detalle": None,
        "agente": "system",
        "ts": _NOW,
    }


@pytest.mark.asyncio
async def test_list_eventos_without_token_is_401(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [_evento_row()]

    response = await client.get(_PATH, headers=TENANT_HEADERS)  # sin X-Internal-Token

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing_internal_token"
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_list_eventos_with_wrong_token_is_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [_evento_row()]

    response = await client.get(
        _PATH, headers={**TENANT_HEADERS, "X-Internal-Token": "wrong-token"}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "invalid_internal_token"
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_list_eventos_token_not_configured_is_401(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: INTERNAL_SERVICE_TOKEN vacío rechaza incluso con header."""
    client, mock_conn, _ = client_with_conn
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", "")
    mock_conn.fetch.return_value = [_evento_row()]

    response = await client.get(
        _PATH, headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "internal_access_not_configured"


@pytest.mark.asyncio
async def test_list_eventos_with_correct_token_returns_200(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [_evento_row()]

    response = await client.get(
        _PATH, headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["object_ref"] == "EXP-2024-0001"


@pytest.mark.asyncio
async def test_list_eventos_with_correct_token_and_no_events_returns_empty_list(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []

    response = await client.get(
        _PATH, headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
    )

    assert response.status_code == 200
    assert response.json() == []
