# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Fixtures E2E — conectan contra los servicios reales corriendo en Docker.

Las URLs se leen de variables de entorno para soportar dos modos:
  - Dentro del stack Docker (pytest en contenedor): usa nombres de servicio
  - Desde el host (pytest local): usa localhost:810x
"""

import os
import uuid
import pytest
import httpx

# URLs configurables por env vars (default: nombres de servicio Docker internos)
TENANT_URL       = os.getenv("E2E_TENANT_URL",       "http://tenant-service:8002")
DOCUMENT_URL     = os.getenv("E2E_DOCUMENT_URL",      "http://document-service:8003")
ARCHIVE_URL      = os.getenv("E2E_ARCHIVE_URL",       "http://archive-service:8004")
STORAGE_URL      = os.getenv("E2E_STORAGE_URL",       "http://storage-service:8005")
WORKFLOW_URL     = os.getenv("E2E_WORKFLOW_URL",      "http://workflow-service:8006")
NOTIFICATION_URL = os.getenv("E2E_NOTIFICATION_URL",  "http://notification-service:8007")

# Tenant de prueba — inicializado por run-e2e.sh antes de correr pytest
E2E_TENANT_SLUG = "e2etest"
E2E_TENANT_CODE = "E2ETEST"
E2E_USER_ID     = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))

TENANT_HEADERS = {
    "X-Tenant-Slug": E2E_TENANT_SLUG,
    "X-User-Id":     E2E_USER_ID,
}


@pytest.fixture(scope="session")
def http():
    """Cliente httpx síncrono reutilizable para toda la sesión."""
    with httpx.Client(timeout=15) as client:
        yield client


@pytest.fixture(scope="session")
def tenant_url():
    return TENANT_URL

@pytest.fixture(scope="session")
def document_url():
    return DOCUMENT_URL

@pytest.fixture(scope="session")
def archive_url():
    return ARCHIVE_URL

@pytest.fixture(scope="session")
def storage_url():
    return STORAGE_URL

@pytest.fixture(scope="session")
def workflow_url():
    return WORKFLOW_URL

@pytest.fixture(scope="session")
def notification_url():
    return NOTIFICATION_URL

@pytest.fixture(scope="session")
def tenant_headers():
    return TENANT_HEADERS

@pytest.fixture(scope="session")
def e2e_tenant_slug():
    return E2E_TENANT_SLUG
