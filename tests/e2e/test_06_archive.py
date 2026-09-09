# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
E2E — Fase 6: archive-service — TRD, expedientes, vinculación de radicados.
"""

import pytest
import httpx

from tests.e2e.conftest import TENANT_HEADERS


def _create_radicado(http: httpx.Client, document_url: str) -> dict:
    r = http.post(
        f"{document_url}/api/v1/documents",
        headers=TENANT_HEADERS,
        json={"doc_type": "E", "subject": "Radicado para expediente E2E", "pages": 1},
    )
    assert r.status_code == 201
    return r.json()


def test_create_trd_serie(http: httpx.Client, archive_url: str) -> None:
    """Crea una serie documental en la TRD."""
    r = http.post(
        f"{archive_url}/api/v1/trd",
        headers=TENANT_HEADERS,
        json={
            "code": "E2E-100",
            "name": "Contratos E2E",
            "retention_years": 5,
            "total_retention": 10,
            "disposition": "conserve",
        },
    )
    assert r.status_code in (201, 409), r.text
    if r.status_code == 201:
        body = r.json()
        assert body["code"] == "E2E-100"
        assert body["disposition"] == "conserve"


def test_list_trd_series(http: httpx.Client, archive_url: str) -> None:
    r = http.get(
        f"{archive_url}/api/v1/trd",
        headers=TENANT_HEADERS,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_expediente(http: httpx.Client, archive_url: str) -> None:
    """Crea un expediente y verifica el código autogenerado."""
    r = http.post(
        f"{archive_url}/api/v1/expedientes",
        headers=TENANT_HEADERS,
        json={"name": "Expediente E2E Test", "description": "Expediente de prueba E2E"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "open"
    assert body["code"].startswith("EXP-")
    assert len(body["code"].split("-")) == 3


def test_link_radicado_to_expediente(
    http: httpx.Client, archive_url: str, document_url: str
) -> None:
    """Vincula un radicado a un expediente y verifica la lista."""
    doc = _create_radicado(http, document_url)

    r_exp = http.post(
        f"{archive_url}/api/v1/expedientes",
        headers=TENANT_HEADERS,
        json={"name": "Expediente con radicado E2E"},
    )
    assert r_exp.status_code == 201
    exp_id = r_exp.json()["id"]

    r_link = http.post(
        f"{archive_url}/api/v1/expedientes/{exp_id}/radicados",
        headers=TENANT_HEADERS,
        json={
            "radicado_id": doc["id"],
            "tracking_number": doc["tracking_number"],
        },
    )
    assert r_link.status_code == 201, r_link.text

    r_get = http.get(
        f"{archive_url}/api/v1/expedientes/{exp_id}",
        headers=TENANT_HEADERS,
    )
    assert r_get.status_code == 200
    radicados = r_get.json()["radicados"]
    assert any(rad["radicado_id"] == doc["id"] for rad in radicados)


def test_close_expediente(http: httpx.Client, archive_url: str) -> None:
    """Cierra un expediente (open → closed)."""
    r = http.post(
        f"{archive_url}/api/v1/expedientes",
        headers=TENANT_HEADERS,
        json={"name": "Para cerrar E2E"},
    )
    exp_id = r.json()["id"]

    r2 = http.patch(
        f"{archive_url}/api/v1/expedientes/{exp_id}",
        headers=TENANT_HEADERS,
        json={"status": "closed"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "closed"
    assert r2.json()["closed_at"] is not None


def test_reopen_expediente_rejected(http: httpx.Client, archive_url: str) -> None:
    """No se puede reabrir un expediente cerrado."""
    r = http.post(
        f"{archive_url}/api/v1/expedientes",
        headers=TENANT_HEADERS,
        json={"name": "Para reabrir E2E"},
    )
    exp_id = r.json()["id"]
    http.patch(
        f"{archive_url}/api/v1/expedientes/{exp_id}",
        headers=TENANT_HEADERS,
        json={"status": "closed"},
    )

    r3 = http.patch(
        f"{archive_url}/api/v1/expedientes/{exp_id}",
        headers=TENANT_HEADERS,
        json={"status": "open"},
    )
    assert r3.status_code in (400, 422)
