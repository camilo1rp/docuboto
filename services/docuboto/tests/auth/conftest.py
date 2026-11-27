# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for auth-service.

The DB dependency (get_db_conn) is overridden with an AsyncMock connection
so tests run without a real PostgreSQL instance.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from orpycamcp_common import usuario
from orpycamcp_common.database import get_conn as get_db_conn
from orpycamcp_common.usuario import instalar_inyector

from tests.auth.app import app

instalar_inyector(app)


@pytest.fixture(autouse=True)
def _bearer_aceptado_por_el_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """El `UsuarioActualMiddleware` da por bueno cualquier Bearer.

    Aquí no hay `app.state.db_pool`, así que el middleware rechazaría todo Bearer
    con 401 antes de llegar al router. La verificación del token la cubren
    `test_identidad.py` y las pruebas de `shared/`.
    """

    async def _valido(scope: dict[str, Any], token: str) -> dict[str, Any]:
        return {"valid": True}

    monkeypatch.setattr(usuario, "_validar", _valido)



class _FakeTx:
    """Context manager async no-op para simular conn.transaction()."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # SELECT 1 (health) / SELECT hash (audit) → no rompe
    conn.execute.return_value = None  # INSERT / advisory lock → no-op
    # orpycamcp_common.audit.append() envuelve el INSERT en `async with conn.transaction()`.
    conn.transaction = MagicMock(return_value=_FakeTx())
    return conn


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
