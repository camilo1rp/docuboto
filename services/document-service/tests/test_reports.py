# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de reportes/estadísticas (E09, F2) + export CSV."""

import pytest

from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
async def test_radicado_report(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # fetch: by_type, by_status, by_month, by_dependencia (en ese orden).
    mock_conn.fetch.side_effect = [
        [{"doc_type": "E", "n": 5}, {"doc_type": "S", "n": 3}],
        [{"status": "registered", "n": 6}, {"status": "archived", "n": 2}],
        [{"month": "2024-01", "n": 4}, {"month": "2024-02", "n": 4}],
        [{"dep": "Jurídica", "n": 5}, {"dep": "(sin asignar)", "n": 3}],
    ]

    resp = await client.get("/api/v1/reports/radicados", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 8
    assert body["by_doc_type"] == {"E": 5, "S": 3}
    assert body["by_status"]["registered"] == 6
    assert body["by_month"][1] == {"month": "2024-02", "count": 4}
    assert body["by_dependencia"]["Jurídica"] == 5


@pytest.mark.asyncio
async def test_radicado_report_date_filter(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.side_effect = [[], [], [], []]

    resp = await client.get(
        "/api/v1/reports/radicados?date_from=2024-01-01&date_to=2024-12-31",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    sql = mock_conn.fetch.call_args_list[0][0][0]
    assert "registered_at >=" in sql and "registered_at <=" in sql


@pytest.mark.asyncio
async def test_radicado_report_csv(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.side_effect = [
        [{"doc_type": "E", "n": 5}],
        [{"status": "registered", "n": 5}],
        [{"month": "2024-01", "n": 5}],
        [{"dep": "Jurídica", "n": 5}],
    ]

    resp = await client.get("/api/v1/reports/radicados.csv", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=radicados.csv" in resp.headers["content-disposition"]
    text = resp.text
    assert "seccion,clave,valor" in text
    assert "tipo,E,5" in text
    assert "dependencia,Jurídica,5" in text
