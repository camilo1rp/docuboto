# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del modelo TRD/CCD: subseries y cálculo de retención (E04, ADR-015)."""

import uuid

import pytest

from tests.conftest import TENANT_HEADERS, make_trd_serie_record


@pytest.mark.asyncio
async def test_create_subserie_with_two_phase_retention(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    parent = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="100.1",
        name="Contratos de obra",
        disposition="CT",
        parent_id=parent,
        archivo_gestion_years=3,
        archivo_central_years=7,
    )

    resp = await client.post(
        "/api/v1/trd",
        json={
            "code": "100.1",
            "name": "Contratos de obra",
            "disposition": "CT",
            "parent_id": str(parent),
            "archivo_gestion_years": 3,
            "archivo_central_years": 7,
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["parent_id"] == str(parent)
    assert body["archivo_gestion_years"] == 3
    assert body["disposition"] == "CT"


@pytest.mark.asyncio
async def test_compute_retention_schedule(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # serie: 3 años gestión + 7 central, disposición conserve→CT.
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="100",
        disposition="conserve",
        archivo_gestion_years=3,
        archivo_central_years=7,
    )
    serie_id = make_trd_serie_record()["id"]

    resp = await client.get(
        f"/api/v1/trd/{serie_id}/retention?closed_at=2024-01-15", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fin_archivo_gestion"] == "2027-01-15"  # +3
    assert body["fin_archivo_central"] == "2034-01-15"  # +3+7
    assert body["disposition_code"] == "CT"
    assert body["disposition_label"] == "Conservación Total"


@pytest.mark.asyncio
async def test_compute_retention_serie_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    resp = await client.get(
        f"/api/v1/trd/{uuid.uuid4()}/retention?closed_at=2024-01-15", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
