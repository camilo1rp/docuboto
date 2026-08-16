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

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

# Permisos que el `/auth/me` simulado concede por defecto. Se declaran aquí y no
# en cada test porque el router resuelve la identidad de VERDAD desde que dejó de
# leer `X-User-Permissions` —cabecera que nadie producía— y esa resolución es
# ahora parte del camino de cualquier invocación.
PERMISOS_POR_DEFECTO = ["PERM_RADI"]
USER_ID = "11111111-1111-1111-1111-111111111111"


def _me(permisos: list[str], roles: list[str] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "user_id": USER_ID,
            "username": "operador",
            "tenant_slug": "demo",
            "roles": roles or [],
            "permissions": permisos,
            "permission_levels": {p: 3 for p in permisos},
        },
    )


@pytest_asyncio.fixture
async def client_with_gateway() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Cede (client, gateway_mock).

    `gateway.get` responde `/auth/me` (identidad) y lo que el test configure;
    `gateway.request` es el camino de los despachos de tool. Mantenerlos
    separados es lo que permite que `request.assert_not_called()` signifique
    «no se despachó nada» sin vaciarse cuando una tool cambia de método.
    """
    gateway = AsyncMock()

    async def _get(path: str, **kwargs: object) -> httpx.Response:
        if path == "/api/v1/auth/me":
            return _me(PERMISOS_POR_DEFECTO)
        return httpx.Response(200, json={})

    gateway.get.side_effect = _get
    app.state.gateway_client = gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, gateway
