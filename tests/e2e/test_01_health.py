# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
E2E — Fase 1: todos los servicios responden /health con status ok.
"""

import pytest
import httpx

from tests.e2e.conftest import (
    TENANT_URL, DOCUMENT_URL, ARCHIVE_URL,
    STORAGE_URL, WORKFLOW_URL, NOTIFICATION_URL,
)

SERVICES = [
    ("tenant-service",       TENANT_URL),
    ("document-service",     DOCUMENT_URL),
    ("archive-service",      ARCHIVE_URL),
    ("storage-service",      STORAGE_URL),
    ("workflow-service",     WORKFLOW_URL),
    ("notification-service", NOTIFICATION_URL),
]


@pytest.mark.parametrize("name,base_url", SERVICES)
def test_service_health(http: httpx.Client, name: str, base_url: str) -> None:
    """Cada servicio responde /health con status ok o degraded (no error)."""
    r = http.get(f"{base_url}/health")
    assert r.status_code == 200, f"{name}: esperaba 200, recibí {r.status_code} — {r.text}"
    body = r.json()
    assert body.get("status") in ("ok", "degraded"), (
        f"{name}: status inesperado: {body}"
    )
