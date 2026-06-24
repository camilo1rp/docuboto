# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del gestor de token OAuth por sesión (E18)."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.services.oauth import TokenManager


def _client(resp_by_path: dict) -> AsyncMock:
    client = AsyncMock()

    async def post(path, json=None, **kw):
        return resp_by_path[path]

    client.post.side_effect = post
    return client


@pytest.mark.asyncio
async def test_static_bearer_precede(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mcp_bearer_token", "estatico")
    client = AsyncMock()
    tm = TokenManager()
    assert await tm.authorization(client) == "Bearer estatico"
    client.post.assert_not_called()  # no llama a auth


@pytest.mark.asyncio
async def test_ropc_obtiene_y_cachea(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mcp_bearer_token", "")
    monkeypatch.setattr(settings, "mcp_refresh_token", "")
    monkeypatch.setattr(settings, "mcp_auth_username", "ana")
    monkeypatch.setattr(settings, "mcp_auth_password", "secreta")
    monkeypatch.setattr(settings, "mcp_tenant_slug", "icetex")
    client = _client(
        {
            "/api/v1/auth/token": httpx.Response(
                200, json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 300}
            )
        }
    )
    tm = TokenManager()
    assert await tm.authorization(client) == "Bearer AT1"
    # llamó a ROPC con las credenciales + tenant
    args = client.post.call_args
    assert args.args[0] == "/api/v1/auth/token"
    assert args.kwargs["json"]["tenant_slug"] == "icetex"
    # segunda llamada: cacheada, no vuelve a pedir token
    client.post.reset_mock()
    assert await tm.authorization(client) == "Bearer AT1"
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_grant(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mcp_bearer_token", "")
    monkeypatch.setattr(settings, "mcp_refresh_token", "RT-INI")
    client = _client(
        {
            "/api/v1/auth/refresh": httpx.Response(
                200, json={"access_token": "AT2", "refresh_token": "RT2", "expires_in": 300}
            )
        }
    )
    tm = TokenManager()
    assert await tm.authorization(client) == "Bearer AT2"
    assert client.post.call_args.args[0] == "/api/v1/auth/refresh"


@pytest.mark.asyncio
async def test_sin_credenciales_devuelve_none(monkeypatch) -> None:
    for k in ("mcp_bearer_token", "mcp_refresh_token", "mcp_auth_username", "mcp_auth_password"):
        monkeypatch.setattr(settings, k, "")
    tm = TokenManager()
    assert await tm.authorization(AsyncMock()) is None
