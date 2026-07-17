# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""GET /api/v1/expedientes/{expediente_id}/aip — lectura mediada del AIP de
preservación (E10 Increment C, cierre del no-read-up de
`GET /preservacion/aip/{id}` en storage-service, RF-SEG-08).

Call sequence (ArchiveService.get_aip):
  fetchval — D-08 gate (require_permission)
  fetchrow — resolve_caller_clearance (caller_clearance)
  fetchrow — get_expediente_by_id (visibilidad, filtrada por clearance)
  [storage_client.get_aip — HTTP mediado, mockeado aparte]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.clients.storage_client import StorageAipError
from tests.conftest import EXPEDIENTE_ID, TENANT_HEADERS, make_expediente_record

_NOW = datetime.now(tz=UTC)


def _aip_payload(*, expediente_id: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "expediente_id": expediente_id,
        "aip_sha256": "a" * 64,
        "indice_sha256": "b" * 64,
        "num_documentos": 2,
        "estado": "completo",
        "created_at": _NOW.isoformat(),
        "premis_xml": "<premis/>",
        "bucket": "orpycamcp-demo-preservation",
        "object_key": f"{expediente_id}/aip.zip",
        "aip_bytes": 4096,
        "formato": "application/zip",
        "retain_until": None,
        "retention_mode": "COMPLIANCE",
        "legal_hold": False,
    }


@pytest.mark.asyncio
async def test_get_aip_sufficient_clearance_returns_aip(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Clearance CLASIFICADA (root) sobre un expediente CLASIFICADA: se media
    la llamada a storage-service con el UUID del expediente en texto."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [
        {"is_root": True, "max_level": None},  # resolve_caller_clearance
        exp,  # get_expediente_by_id (visible: 3 <= 3)
    ]
    mock_conn.fetch.return_value = []
    payload = _aip_payload(expediente_id=str(EXPEDIENTE_ID))

    with patch(
        "app.clients.storage_client.get_aip", new=AsyncMock(return_value=payload)
    ) as mock_get_aip:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/aip", headers=TENANT_HEADERS
        )

    assert response.status_code == 200
    body = response.json()
    assert body["expediente_id"] == str(EXPEDIENTE_ID)
    assert body["aip_sha256"] == "a" * 64
    mock_get_aip.assert_awaited_once_with(
        tenant_slug="demo", expediente_id=str(EXPEDIENTE_ID)
    )


@pytest.mark.asyncio
async def test_get_aip_deja_asiento_de_acceso(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    # La lectura del AIP (inventario clasificado) deja pista de acceso
    # (RF-BUS-10) cuando hay canal auditable + actor real.
    from app.services import archive_service as _svc

    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [{"is_root": True, "max_level": None}, exp]
    mock_conn.fetch.return_value = []
    payload = _aip_payload(expediente_id=str(EXPEDIENTE_ID))
    append = AsyncMock()

    with (
        patch("app.clients.storage_client.get_aip", new=AsyncMock(return_value=payload)),
        patch.object(_svc.audit, "append", append),
    ):
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/aip",
            headers={**TENANT_HEADERS, "X-Canal": "ui"},
        )

    assert response.status_code == 200
    append.assert_awaited_once()
    kw = append.await_args.kwargs
    assert kw["action"] == "archive.aip_consultado"
    assert kw["object_type"] == "expediente"
    assert kw["canal"] == "ui"


@pytest.mark.asyncio
async def test_get_aip_over_clearance_returns_404_neutral(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """RF-SEG-08 no-read-up: llamante PUBLICA (1) sobre un expediente
    CLASIFICADA (3) — 404 neutro, storage-service NUNCA se llama."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate granted
    # 1st fetchrow -> resolve_caller_clearance -> sin membresías -> PUBLICA(1)
    # 2nd fetchrow -> get_expediente_by_id filtrado por clearance -> None
    mock_conn.fetchrow.side_effect = [None, None]

    with patch(
        "app.clients.storage_client.get_aip", new=AsyncMock()
    ) as mock_get_aip:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/aip", headers=TENANT_HEADERS
        )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"
    mock_get_aip.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_aip_expediente_does_not_exist_returns_404_neutral(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Expediente realmente inexistente — MISMO cuerpo 404 que el caso de
    sobre-clearance (indistinguibles a propósito, no-read-up)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate granted
    mock_conn.fetchrow.side_effect = [{"is_root": True, "max_level": None}, None]

    with patch(
        "app.clients.storage_client.get_aip", new=AsyncMock()
    ) as mock_get_aip:
        response = await client.get(
            f"/api/v1/expedientes/{uuid.uuid4()}/aip", headers=TENANT_HEADERS
        )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"
    mock_get_aip.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_aip_visible_expediente_without_aip_returns_404_neutral(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Expediente visible para el llamante pero SIN AIP empaquetado todavía
    (storage-service responde 404 -> storage_client.get_aip devuelve None):
    MISMO cuerpo 404 que los dos casos anteriores — ningún oráculo de "¿hay
    un AIP para este expediente clasificado?"."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchval.return_value = True  # D-08 gate granted
    mock_conn.fetchrow.side_effect = [None, exp]  # PUBLICA(1) caller, expediente PUBLICA
    mock_conn.fetch.return_value = []

    with patch(
        "app.clients.storage_client.get_aip", new=AsyncMock(return_value=None)
    ) as mock_get_aip:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/aip", headers=TENANT_HEADERS
        )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"
    mock_get_aip.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_aip_storage_unavailable_returns_503(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un fallo de storage-service (red/timeout/HTTP inesperado) es ortogonal
    a la autorización: 503, nunca un 404 que lo confundiría con "no
    autorizado"."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchval.return_value = True  # D-08 gate granted
    mock_conn.fetchrow.side_effect = [None, exp]
    mock_conn.fetch.return_value = []

    with patch(
        "app.clients.storage_client.get_aip",
        new=AsyncMock(side_effect=StorageAipError("storage-service inalcanzable")),
    ):
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/aip", headers=TENANT_HEADERS
        )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "storage_unavailable"


@pytest.mark.asyncio
async def test_get_aip_requires_permission_gate(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """D-08: sin USUA_PERM_EXPEDIENTE, 403 antes de tocar caller_clearance o
    storage-service."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # D-08 gate denied

    with patch(
        "app.clients.storage_client.get_aip", new=AsyncMock()
    ) as mock_get_aip:
        response = await client.get(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/aip", headers=TENANT_HEADERS
        )

    assert response.status_code == 403
    mock_get_aip.assert_not_awaited()
