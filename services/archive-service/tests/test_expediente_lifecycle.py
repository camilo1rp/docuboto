# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del ciclo de vida del expediente: cierre/transferencia/índice (E02)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    TENANT_HEADERS,
    make_expediente_record,
    make_radicado_link_record,
    make_trd_serie_record,
)


@pytest.mark.asyncio
async def test_close_applies_trd_disposition(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=serie_id, exp_status="open")
    trd = make_trd_serie_record(
        disposition="conserve", archivo_gestion_years=3, archivo_central_years=7
    )
    exp_closed = make_expediente_record(
        exp_id=exp_id,
        trd_serie_id=serie_id,
        exp_status="closed",
        disposition={"disposition_code": "CT", "disposition_label": "Conservación Total"},
    )
    # fetchrow: exp(open) → trd → exp(closed update) → exp(reload).
    # El índice (regenerar+firmar) se aísla parcheando IndexService.
    mock_conn.fetchrow.side_effect = [exp_open, trd, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["disposition"]["disposition_code"] == "CT"


@pytest.mark.asyncio
async def test_firmar_indice_marca_firmado(client_with_conn) -> None:
    """El índice vigente se firma vía signature-service y queda 'firmado'."""
    client, mock_conn = client_with_conn  # noqa: F841 (solo para overrides de app)
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    svc = IndexService(mock_conn, "demo")
    # get_current → índice vigente; mark_signed → execute
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>"}

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock(return_value=str(firma_id))):
        result = await svc.firmar_indice(exp_id, tenant_slug="demo", firmante_id=None)
    assert result == firma_id
    # Se ejecutó el UPDATE de mark_signed (estado='firmado').
    assert any("estado='firmado'" in str(c.args[0]) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_close_invalid_state(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="closed")
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{uuid.uuid4()}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_transfer_from_closed(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    transferred = make_expediente_record(exp_id=exp_id, exp_status="transferred")
    mock_conn.fetchrow.side_effect = [closed, transferred, transferred]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "transferred"


@pytest.mark.asyncio
async def test_indice_foliation(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=exp_id)
    mock_conn.fetch.return_value = [
        make_radicado_link_record(radicado_id=uuid.uuid4(), tracking_number="2024-A-001"),
        make_radicado_link_record(radicado_id=uuid.uuid4(), tracking_number="2024-A-002"),
    ]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/foliado", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_folios"] == 2
    assert body["items"][0]["folio"] == 1
    assert body["items"][1]["folio"] == 2
