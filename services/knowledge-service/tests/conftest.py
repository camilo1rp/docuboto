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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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


# Desde ADR-037 las rutas de consulta exigen identidad (``get_actor``): 401 sin X-User-Id.
USER_ID = "00000000-0000-4000-8000-0000000000a1"
TENANT_HEADERS = {"X-Tenant-Slug": "demo", "X-User-Id": USER_ID}

@pytest_asyncio.fixture
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    # `conn.transaction()` de asyncpg es un context manager async (no un
    # coroutine): el default de AsyncMock lo haría awaitable. Se sustituye por una
    # factory que devuelve un CM async no-op reentrante.
    @asynccontextmanager
    async def _fake_tx() -> AsyncGenerator[None, None]:
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **k: _fake_tx())

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _gates_abiertos(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abre los dos gates nuevos (ADR-037) para que las suites de lógica no dependan de ellos.

    Quien prueba los gates en sí lo declara con ``@pytest.mark.gates_reales``: entonces
    ``check_permission`` y la lectura de la bandera son los de verdad (contra el ``conn``
    simulado de cada prueba).
    """
    if request.node.get_closest_marker("gates_reales"):
        return
    from app.core import clearance

    monkeypatch.setattr(clearance, "check_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(clearance, "asistente_habilitado", AsyncMock(return_value=True))
