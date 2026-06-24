# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Fixtures del mcp-server: cliente del gateway mockeado (no se llama a la red)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client_with_gateway() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Cede (client, gateway_mock). gateway_mock.get/post son AsyncMock configurables."""
    gateway = AsyncMock()
    app.state.gateway_client = gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, gateway
