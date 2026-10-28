# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de mcp-server.

`/health` devolvía `{"status": "ok"}` fijo y era el `healthcheck` del contenedor.
mcp-server no tiene BD ni Redis: su única dependencia real es que el api-gateway
conteste. Sin esa comprobación, la fachada MCP se reportaba sana cuando ninguna
herramienta podía ejecutarse.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.asyncio


async def _get(ruta: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(ruta)


def _cliente_que(respuesta=None, error=None):
    cliente = AsyncMock()
    if error is not None:
        cliente.get.side_effect = error
    else:
        cliente.get.return_value = respuesta
    return AsyncMock(
        __aenter__=AsyncMock(return_value=cliente), __aexit__=AsyncMock(return_value=False)
    )


async def test_health_es_vivacidad_y_no_llama_al_gateway() -> None:
    with patch("app.routers.health.httpx.AsyncClient") as cliente:
        respuesta = await _get("/health")
        assert cliente.call_count == 0

    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok", "service": "mcp-server"}


async def test_ready_comprueba_el_gateway() -> None:
    with patch(
        "app.routers.health.httpx.AsyncClient",
        # Con `request=` puesta: sin ella, `raise_for_status()` de httpx lanza
        # un RuntimeError y el test verde/rojo mediría el doble, no el código.
        return_value=_cliente_que(
            httpx.Response(
                200,
                json={"status": "ok"},
                request=httpx.Request("GET", "http://api-gateway:8080/health"),
            )
        ),
    ):
        respuesta = await _get("/health/ready")

    assert respuesta.status_code == 200
    assert respuesta.json()["checks"] == {"gateway": "ok"}


async def test_ready_devuelve_503_con_el_gateway_caido() -> None:
    """Era el caso que se reportaba «ok»: sin gateway no hay ni una herramienta."""
    with patch(
        "app.routers.health.httpx.AsyncClient",
        return_value=_cliente_que(error=httpx.ConnectError("sin ruta")),
    ):
        respuesta = await _get("/health/ready")

    assert respuesta.status_code == 503
    assert respuesta.json()["checks"]["gateway"] == "error"


async def test_ready_no_publica_la_direccion_del_gateway() -> None:
    with patch(
        "app.routers.health.httpx.AsyncClient",
        return_value=_cliente_que(
            error=httpx.ConnectError("Name or service not known: api-gateway:8080")
        ),
    ):
        cuerpo = (await _get("/health/ready")).text

    assert "api-gateway:8080" not in cuerpo
