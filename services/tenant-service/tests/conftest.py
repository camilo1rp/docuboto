# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for tenant-service.

The DB dependency (get_db_conn) is overridden with an AsyncMock connection
so tests run without a real PostgreSQL instance.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn
from app.main import app

# Las pruebas unitarias llaman a la aplicación DIRECTAMENTE, sin gateway.
#
# Desde que la identidad va firmada, eso es exactamente lo que el servicio
# rechaza en producción: una petición con cabeceras de usuario que nadie firmó.
# Aquí se corre en el modo «firma no activada», que es una configuración
# legítima —la que tiene un despliegue que aún no puso el secreto— y no un
# atajo: la firma en sí la cubren `shared/tests/test_la_identidad_viene_firmada.py`
# y la comprobación contra la pila levantada.
os.environ.pop("IDENTITY_SIGNING_SECRET", None)



@asynccontextmanager
async def _noop_tx() -> AsyncGenerator[None, None]:
    yield


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(side_effect=lambda *a, **k: _noop_tx())
    conn.fetchval.return_value = 1  # SELECT 1 → health check passes
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    return conn


def make_tenant_record(
    *,
    slug: str = "demo",
    name: str = "Demo Institution",
    code: str = "DEMO",
    status: str = "active",
    tenant_id: uuid.UUID | None = None,
) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": tenant_id or uuid.uuid4(),
        "slug": slug,
        "name": name,
        "code": code,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


# D-02: `/api/v1/tenants` (registro de instituciones, sobre `public.tenants`)
# está sellado servicio-a-servicio. Vacío = fail-closed, así que los tests que
# ejercitan el comportamiento del recurso deben configurar el token Y enviarlo;
# los que ejercitan el GATE en sí lo omiten a propósito.
TEST_INTERNAL_TOKEN = "test-tenant-internal-token-xxx"
INTERNAL_HEADERS = {"X-Internal-Token": TEST_INTERNAL_TOKEN}


@pytest.fixture(autouse=True)
def _configure_internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configura el token interno para toda la suite.

    Es autouse porque la alternativa —recordar añadirlo en cada test nuevo— es
    la forma que se olvida. Solo lo consume el router de tenants; para los demás
    (dependencias, catálogos, PINAR…) es inerte.
    """
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) for tests that need to configure mock_conn behaviour."""
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    # El alta y el cambio de estado de una entidad asientan en `public.audit_log`
    # (Canal A); el asiento se intercepta aquí y se expone en `mock_conn.audit_append`.
    with patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as audit_append:
        mock_conn.audit_append = audit_append
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, mock_conn

    app.dependency_overrides.clear()
