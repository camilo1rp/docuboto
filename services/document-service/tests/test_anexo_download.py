# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de la descarga mediada de anexos (D-02, RF-SEG-08).

GET /api/v1/documents/{document_id}/anexos/{file_id}/download

Cierra la fuga de no-read-up en `GET storage/files/{id}/download` (presigned
sin clearance): document-service ahora media TODA descarga, pidiendo bytes a
storage-service (mockeado aquí vía `app.clients.storage_client`) solo después
de validar clearance + pertenencia del anexo al radicado.

Orden de llamadas a la conexión mockeada por request (camino feliz):

  fetchrow #1 — resolve_caller_clearance (SELECT is_root, max_level)
  fetchrow #2 — get_anexo_for_download (JOIN anexos/radicados, no-read-up +
                pertenencia en una sola consulta)
  [storage_client.open_content_stream — mockeado, sin tocar mock_conn]
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.clients import storage_client
from tests.conftest import TENANT_HEADERS, make_anexo_record

DOC_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
FILE_ID = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
_CONTENT = b"contenido-del-anexo"


def _clearance_row(max_level: int = 1, is_root: bool = False) -> dict:
    return {"is_root": is_root, "max_level": max_level}


class _FakeStream:
    """Doble de `storage_client.StorageContentStream` para los tests."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self.closed = False

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self._content

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Caso 1 — descarga exitosa (clearance suficiente)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_anexo_success(client_with_conn, monkeypatch) -> None:
    client, mock_conn = client_with_conn
    anexo_row = make_anexo_record(radicado_id=DOC_ID)
    anexo_row["file_id"] = FILE_ID
    anexo_row["filename"] = "informe.pdf"
    anexo_row["mime_type"] = "application/pdf"
    anexo_row["tracking_number"] = "2024-DEMO-E-000001"
    anexo_row["nivel_seguridad"] = 1

    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),  # resolve_caller_clearance
        anexo_row,  # get_anexo_for_download
    ]

    fake_stream = _FakeStream(_CONTENT)

    async def fake_open_content_stream(*, tenant_slug: str, file_id: uuid.UUID):
        assert tenant_slug == "demo"
        assert file_id == FILE_ID
        return fake_stream

    monkeypatch.setattr(storage_client, "open_content_stream", fake_open_content_stream)

    resp = await client.get(
        f"/api/v1/documents/{DOC_ID}/anexos/{FILE_ID}/download",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.content == _CONTENT
    assert resp.headers["content-type"] == "application/pdf"
    assert "informe.pdf" in resp.headers["content-disposition"]
    # El repositorio recibió exactamente document_id/file_id/clearance.
    second_call = mock_conn.fetchrow.call_args_list[1]
    assert second_call.args[1] == DOC_ID
    assert second_call.args[2] == FILE_ID
    assert second_call.args[3] == 3


# ---------------------------------------------------------------------------
# Caso 2 — no-read-up: clearance insuficiente para el radicado dueño del anexo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_anexo_over_clearance_is_neutral_404(
    client_with_conn, monkeypatch
) -> None:
    """El JOIN `nivel_seguridad <= max_clearance` filtra la fila → None."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=1),  # clearance bajo del llamante
        None,  # get_anexo_for_download: la fila existe en BD pero el filtro la excluye
    ]

    called = False

    async def fake_open_content_stream(**_kwargs):
        nonlocal called
        called = True
        return _FakeStream(_CONTENT)

    monkeypatch.setattr(storage_client, "open_content_stream", fake_open_content_stream)

    resp = await client.get(
        f"/api/v1/documents/{DOC_ID}/anexos/{FILE_ID}/download",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"
    # No debe llegar a pedir bytes a storage-service tras la denegación.
    assert called is False


# ---------------------------------------------------------------------------
# Caso 3 — file_id no es anexo de ESE radicado (oráculo cruzado cerrado)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_anexo_foreign_file_id_is_neutral_404(
    client_with_conn, monkeypatch
) -> None:
    """`file_id` existe (es anexo de OTRO radicado) pero el JOIN no lo liga a
    `document_id` → misma fila `None`, mismo 404 que los demás caminos."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        None,  # el JOIN a.radicado_id=r.id AND r.id=$1 no encuentra fila
    ]

    monkeypatch.setattr(
        storage_client, "open_content_stream", AsyncMock(return_value=_FakeStream(_CONTENT))
    )

    resp = await client.get(
        f"/api/v1/documents/{DOC_ID}/anexos/{FILE_ID}/download",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


# ---------------------------------------------------------------------------
# Caso 4 — radicado inexistente
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_anexo_document_not_found_is_neutral_404(
    client_with_conn, monkeypatch
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        None,
    ]

    monkeypatch.setattr(
        storage_client, "open_content_stream", AsyncMock(return_value=_FakeStream(_CONTENT))
    )

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/anexos/{FILE_ID}/download",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


# ---------------------------------------------------------------------------
# Caso 5 — storage-service ya no tiene el objeto (purgado) — 404 neutro también
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_anexo_storage_object_missing_is_neutral_404(
    client_with_conn, monkeypatch
) -> None:
    client, mock_conn = client_with_conn
    anexo_row = make_anexo_record(radicado_id=DOC_ID)
    anexo_row["file_id"] = FILE_ID
    anexo_row["tracking_number"] = "2024-DEMO-E-000001"
    anexo_row["nivel_seguridad"] = 1
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        anexo_row,
    ]

    monkeypatch.setattr(
        storage_client, "open_content_stream", AsyncMock(return_value=None)
    )

    resp = await client.get(
        f"/api/v1/documents/{DOC_ID}/anexos/{FILE_ID}/download",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


# ---------------------------------------------------------------------------
# Caso 6 — storage-service inalcanzable/erróneo → 503, nunca un 200 vacío
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_anexo_storage_error_is_503(client_with_conn, monkeypatch) -> None:
    client, mock_conn = client_with_conn
    anexo_row = make_anexo_record(radicado_id=DOC_ID)
    anexo_row["file_id"] = FILE_ID
    anexo_row["tracking_number"] = "2024-DEMO-E-000001"
    anexo_row["nivel_seguridad"] = 1
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        anexo_row,
    ]

    async def fake_open_content_stream(**_kwargs):
        raise storage_client.StorageContentError("storage-service inalcanzable: boom")

    monkeypatch.setattr(storage_client, "open_content_stream", fake_open_content_stream)

    resp = await client.get(
        f"/api/v1/documents/{DOC_ID}/anexos/{FILE_ID}/download",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "storage_unavailable"


# ---------------------------------------------------------------------------
# Caso 7 — la descarga AUTORIZADA deja asiento en audit_log (RF-BUS-10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_anexo_deja_asiento_de_descarga(client_with_conn, monkeypatch) -> None:
    # El acceso al CONTENIDO (más fuerte que la lectura del metadato) debe
    # auditarse: canal auditable + actor real -> asiento document.anexo_descargado.
    from app.services import document_service as _svc

    client, mock_conn = client_with_conn
    anexo_row = make_anexo_record(radicado_id=DOC_ID)
    anexo_row["file_id"] = FILE_ID
    anexo_row["tracking_number"] = "2024-DEMO-E-000042"
    anexo_row["nivel_seguridad"] = 2
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=3), anexo_row]
    monkeypatch.setattr(
        storage_client, "open_content_stream", AsyncMock(return_value=_FakeStream(_CONTENT))
    )
    append = AsyncMock()
    monkeypatch.setattr(_svc.audit, "append", append)

    resp = await client.get(
        f"/api/v1/documents/{DOC_ID}/anexos/{FILE_ID}/download",
        headers={**TENANT_HEADERS, "X-Canal": "ui"},
    )

    assert resp.status_code == 200
    append.assert_awaited_once()
    kw = append.await_args.kwargs
    assert kw["action"] == "document.anexo_descargado"
    assert kw["object_ref"] == "2024-DEMO-E-000042"
    assert kw["canal"] == "ui"
    assert kw["payload"]["file_id"] == str(FILE_ID)
    assert kw["payload"]["nivel_seguridad"] == 2


@pytest.mark.asyncio
async def test_download_anexo_sin_canal_no_audita(client_with_conn, monkeypatch) -> None:
    # Sin canal auditable (default 'system') no se registra la descarga.
    from app.services import document_service as _svc

    client, mock_conn = client_with_conn
    anexo_row = make_anexo_record(radicado_id=DOC_ID)
    anexo_row["file_id"] = FILE_ID
    anexo_row["tracking_number"] = "2024-DEMO-E-000042"
    anexo_row["nivel_seguridad"] = 1
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=3), anexo_row]
    monkeypatch.setattr(
        storage_client, "open_content_stream", AsyncMock(return_value=_FakeStream(_CONTENT))
    )
    append = AsyncMock()
    monkeypatch.setattr(_svc.audit, "append", append)

    resp = await client.get(
        f"/api/v1/documents/{DOC_ID}/anexos/{FILE_ID}/download",
        headers=TENANT_HEADERS,  # sin X-Canal
    )

    assert resp.status_code == 200
    append.assert_not_awaited()
