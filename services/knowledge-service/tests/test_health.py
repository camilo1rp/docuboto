# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de knowledge-service.

`/health` devolvía `{"status": "ok"}` fijo y era el `healthcheck` del contenedor.
Aquí engaña doblemente: si el consumidor de ingesta muere, el índice pgvector se
congela en el pasado y el servicio sigue respondiendo búsquedas — con resultados
incompletos y sin que nada lo delate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.asyncio


async def _get(ruta: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(ruta)


def _pool_que(resultado=None, error=None):
    conn = AsyncMock()
    if error is not None:
        conn.fetchval.side_effect = error
    else:
        conn.fetchval.return_value = resultado
    pool = MagicMock()
    pool.acquire.return_value = AsyncMock(
        __aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)
    )
    return pool


async def test_health_es_vivacidad_y_no_toca_nada() -> None:
    with patch("app.routers.health.get_pool") as pool:
        respuesta = await _get("/health")
        assert pool.call_count == 0

    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok", "service": "knowledge-service"}


async def test_ready_comprueba_pgvector_y_redis() -> None:
    with (
        patch("app.routers.health.get_pool", return_value=_pool_que(1)),
        patch("app.routers.health.get_redis", return_value=AsyncMock()),
    ):
        respuesta = await _get("/health/ready")

    assert respuesta.status_code == 200
    assert respuesta.json()["checks"] == {"database": "ok", "redis": "ok"}


async def test_ready_devuelve_503_sin_base_de_datos() -> None:
    with (
        patch("app.routers.health.get_pool", return_value=_pool_que(error=OSError("caída"))),
        patch("app.routers.health.get_redis", return_value=AsyncMock()),
    ):
        respuesta = await _get("/health/ready")

    assert respuesta.status_code == 503
    assert respuesta.json()["checks"]["database"] == "error"


async def test_ready_expone_el_consumidor_de_ingesta() -> None:
    """Un consumidor muerto aquí significa un índice desactualizado sirviendo
    búsquedas como si estuviera al día."""
    from orpycamcp_common import events

    events._estado_consumidores["knowledge"] = {
        "corriendo": False,
        "reinicios": 2,
        "ultimo_error": "ConnectionError",
    }
    try:
        with (
            patch("app.routers.health.get_pool", return_value=_pool_que(1)),
            patch("app.routers.health.get_redis", return_value=AsyncMock()),
        ):
            respuesta = await _get("/health/ready")
    finally:
        events._estado_consumidores.clear()

    assert respuesta.json()["checks"]["consumer"] == "reiniciando"


async def test_ready_no_publica_el_motivo_del_fallo() -> None:
    fallo = OSError("connect failed: postgres:5432")
    with (
        patch("app.routers.health.get_pool", return_value=_pool_que(error=fallo)),
        patch("app.routers.health.get_redis", return_value=AsyncMock()),
    ):
        cuerpo = (await _get("/health/ready")).text

    assert "postgres:5432" not in cuerpo
