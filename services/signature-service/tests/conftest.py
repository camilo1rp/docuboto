# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_tenant_conn
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


# El default de `signature_secret` es "" desde que el arranque exige que venga
# del entorno (11.5). Los tests fijan aquí un valor propio: sin él ejercerían el
# HMAC con clave vacía, que es justo la configuración que el guard rechaza.
settings.signature_secret = "test-signature-secret-0123456789abcdef"

# X-User-Id now carries the internal auth_users.id UUID (not keycloak_sub).
# auth-service /validate resolves keycloak_sub → auth_users.id before the gateway
# injects the header; tests use a fixed UUID that matches the mock's user row.
USER = uuid.UUID("11111111-1111-1111-1111-111111111111")

# Both headers are required for every authenticated request.
# X-User-Id = internal auth_users.id (uuid); get_actor + require_permission read it.
TENANT_HEADERS = {"X-Tenant-Slug": "demo", "X-User-Id": str(USER)}


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    # Default True: check_permission (require_permission dep) sees the user as authorised.
    # Tests that need permission denied override with fetchval.return_value = None
    # or side_effect = [None, ...].
    conn.fetchval.return_value = True
    # audit.append uses `async with conn.transaction():` then conn.execute (INSERT).
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


@pytest_asyncio.fixture
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) with PERM_FIRMA granted and tenant conn overridden.

    auth is provided via X-User-Id in TENANT_HEADERS (not via get_current_user —
    that dependency no longer exists in the signature-service routers).
    """
    conn = _make_mock_conn()

    async def _conn_dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.clear()
