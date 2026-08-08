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
@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("delete", f"/api/v1/storage/files/{FILE_ID}"),
        ("get", f"/api/v1/storage/files/{FILE_ID}/verify"),
        ("post", f"/api/v1/storage/files/{FILE_ID}/replace"),
        ("get", f"/api/v1/storage/files/{FILE_ID}/versions"),
    ],
)
async def test_mutaciones_y_verificacion_exigen_token_interno(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    metodo: str,
    ruta: str,
) -> None:
    """Las cuatro rutas que quedaban abiertas ahora exigen `X-Internal-Token`.

    Este test SUSTITUYE a uno que afirmaba lo contrario
    (`test_delete_is_not_gated_by_internal_token`). Aquel no documentaba una
    decisión de diseño sino el ALCANCE del incremento que lo escribió («solo
    bytes/URL/metadata de LECTURA»), y ese alcance dejó fuera precisamente las
    operaciones que MUTAN la evidencia: `replace` sustituye el contenido de un
    anexo y `delete` lo da de baja con `deleted_by = None`, es decir sin actor
    en la traza.

    El único control que tenían era no estar en la tabla de ruteo del gateway
    —una ausencia, no un control— y un salto de ruta `..` la atravesaba (ver
    `api-gateway/app/middleware/path_guard.py`, cerrado en paralelo).

    `POST /upload` sigue SIN gate a propósito: el frontend sube directo y esa
    ruta sí está enrutada por el gateway (ver el test de abajo).
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID)

    response = await getattr(client, metodo)(
        ruta,
        headers=TENANT_HEADERS,  # sin X-Internal-Token
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Remediación de auditoría (Baja): comparación en tiempo constante
# (`hmac.compare_digest`, no `!=`) — se prueba `require_internal_token`
# DIRECTAMENTE (sin HTTP) para cubrir el caso de tokens de LONGITUD DISTINTA,
# que `hmac.compare_digest` maneja sin excepción (a diferencia de un XOR
# byte-a-byte ingenuo) y que ningún test HTTP existente ejercita a propósito.
# ---------------------------------------------------------------------------


def test_require_internal_token_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from app.core.internal_auth import require_internal_token

    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)

    # Token correcto: no lanza.
    require_internal_token(x_internal_token=TEST_INTERNAL_TOKEN)

    # Token incorrecto de la MISMA longitud: 403, no crashea.
    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token="x" * len(TEST_INTERNAL_TOKEN))
    assert exc_info.value.status_code == 403

    # Token incorrecto de longitud DISTINTA (más corto y más largo): sigue
    # siendo un 403 fail-closed, `hmac.compare_digest` no revienta con
    # longitudes distintas (a diferencia de un XOR ingenuo byte-a-byte).
    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token="short")
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token=TEST_INTERNAL_TOKEN + "-extra-suffix")
    assert exc_info.value.status_code == 403
