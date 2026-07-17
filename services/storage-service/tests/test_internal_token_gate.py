# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del gate D-02 (X-Internal-Token) en los endpoints que sirven
bytes/URL/metadata de un archivo — remediación de la fuga de no-read-up
(RF-SEG-08) en la descarga de anexos.

`/upload` queda deliberadamente FUERA del gate (el frontend sube directo);
estos tests lo confirman explícitamente para que una futura regresión que
gatee `/upload` por accidente rompa un test, no solo el frontend en silencio.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.core import config as cfg_module
from tests.conftest import (
    FILE_ID,
    TENANT_HEADERS,
    TEST_INTERNAL_TOKEN,
    make_file_record,
    minio_get_object_stream,
)

_GATED_PATHS = (
    f"/api/v1/storage/files/{FILE_ID}",
    f"/api/v1/storage/files/{FILE_ID}/download",
    f"/api/v1/storage/files/{FILE_ID}/content",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED_PATHS)
async def test_gated_endpoint_without_token_is_401(
    path: str,
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Token configurado pero AUSENTE en la petición → 401 missing_internal_token."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID)

    response = await client.get(path, headers=TENANT_HEADERS)  # sin X-Internal-Token

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing_internal_token"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED_PATHS)
async def test_gated_endpoint_with_wrong_token_is_403(
    path: str,
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID)

    response = await client.get(
        path, headers={**TENANT_HEADERS, "X-Internal-Token": "wrong-token"}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "invalid_internal_token"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED_PATHS)
async def test_gated_endpoint_token_not_configured_is_401(
    path: str,
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: INTERNAL_SERVICE_TOKEN vacío rechaza TODAS las llamadas,
    incluso trayendo un header — no hay modo "abierto" por omisión."""
    client, mock_conn, _ = client_with_conn
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", "")
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID)

    response = await client.get(
        path, headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "internal_access_not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED_PATHS)
async def test_gated_endpoint_with_correct_token_succeeds(
    path: str,
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.return_value = record
    mock_minio.presigned_get_object.return_value = "http://minio/presigned-url"
    mock_minio.get_object = minio_get_object_stream(b"contenido")

    response = await client.get(
        path, headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_upload_is_not_gated_by_internal_token(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/upload sigue expuesto: el frontend sube directo, sin X-Internal-Token,
    incluso cuando el token de servicio interno SÍ está configurado."""
    client, mock_conn, mock_minio = client_with_conn
    mock_conn.fetchrow.side_effect = [None, make_file_record(file_id=FILE_ID)]

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": ("carta.pdf", b"PDF content here", "application/pdf")},
        headers=TENANT_HEADERS,  # sin X-Internal-Token
    )

    assert response.status_code == 201
    mock_minio.put_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_is_not_gated_by_internal_token(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """DELETE tampoco está en el alcance de este incremento (fuera de lo
    pedido por D-02: solo bytes/URL/metadata de LECTURA)."""
    client, mock_conn, mock_minio = client_with_conn
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.return_value = record
    mock_conn.execute.side_effect = [None, None, "UPDATE 1", None, None]

    response = await client.delete(
        f"/api/v1/storage/files/{FILE_ID}",
        headers=TENANT_HEADERS,  # sin X-Internal-Token
    )

    assert response.status_code == 204
