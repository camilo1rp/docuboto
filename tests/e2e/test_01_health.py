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
