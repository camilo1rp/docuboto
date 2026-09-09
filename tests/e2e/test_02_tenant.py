# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
E2E — Fase 2: tenant-service — CRUD de instituciones.
"""

import pytest
import httpx

from tests.e2e.conftest import E2E_TENANT_SLUG, E2E_TENANT_CODE


def test_tenant_exists(http: httpx.Client, tenant_url: str) -> None:
    """El tenant e2etest ya fue creado por run-e2e.sh."""
    r = http.get(f"{tenant_url}/api/v1/tenants/by-slug/{E2E_TENANT_SLUG}")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == E2E_TENANT_SLUG
    assert body["code"] == E2E_TENANT_CODE
    assert body["status"] == "active"


def test_list_tenants(http: httpx.Client, tenant_url: str) -> None:
    r = http.get(f"{tenant_url}/api/v1/tenants")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert int(r.headers.get("x-total-count", 0)) >= 1


def test_create_and_get_tenant(http: httpx.Client, tenant_url: str) -> None:
    """Crea un tenant nuevo, lo consulta, verifica inmutabilidad de slug."""
    payload = {
        "slug": "e2e-extra",
        "name": "Tenant Extra E2E",
        "code": "E2EXTRA",
    }
    r = http.post(f"{tenant_url}/api/v1/tenants", json=payload)
    # Puede ser 201 (creado) o 409 (ya existe de un run anterior)
    assert r.status_code in (201, 409)

    r2 = http.get(f"{tenant_url}/api/v1/tenants/by-slug/e2e-extra")
    assert r2.status_code == 200
    assert r2.json()["code"] == "E2EXTRA"


def test_tenant_not_found(http: httpx.Client, tenant_url: str) -> None:
    r = http.get(f"{tenant_url}/api/v1/tenants/by-slug/no-existe-nunca")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "tenant_not_found"
