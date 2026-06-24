# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de anulación de radicado en dos pasos (paridad legado)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
_USER = str(uuid.uuid4())


def _anulacion_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "estado": "solicitada",
        "causal": "ANU-ERR",
        "motivo": "Error de radicación",
        "solicitado_por": uuid.UUID(_USER),
        "solicitado_at": NOW,
        "decidido_por": None,
        "decidido_at": None,
        "observacion": None,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_solicitar_anulacion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = "registered"  # radicado_status → vigente
    mock_conn.fetchrow.return_value = _anulacion_row(radicado_id=rid)

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Error de radicación"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "solicitada"


@pytest.mark.asyncio
async def test_solicitar_sobre_anulado_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = "anulado"
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion",
        json={"causal": "ANU-DUP"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "ya_anulado"


@pytest.mark.asyncio
async def test_aprobar_anula_el_radicado(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    # get_active (solicitada) → decide (UPDATE RETURNING aprobada)
    mock_conn.fetchrow.side_effect = [
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobada"


@pytest.mark.asyncio
async def test_aprobar_sin_solicitud_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_active → None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion/aprobar",
        json={},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "sin_solicitud_activa"
