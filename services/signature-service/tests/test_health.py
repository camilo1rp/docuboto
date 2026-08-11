# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de signature-service.

`/health` devolvía `{"status": "ok"}` **fijo, sin tocar nada**, y era el
`healthcheck` del contenedor: el servicio que firma el índice electrónico y sella
las actas de transferencia se reportaba sano con la base de datos caída.
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
    """Pool falso cuyo `acquire()` entrega una conexión que responde o falla."""
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
    """Gobierna los reinicios: si dependiera de Postgres, un Postgres caído
    provocaría una tormenta de reinicios que no arregla Postgres."""
    with patch("app.routers.health.get_pool") as pool:
        respuesta = await _get("/health")
        assert pool.call_count == 0

    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok", "service": "signature-service"}


async def test_ready_comprueba_base_de_datos_y_redis() -> None:
    redis = AsyncMock()
    with (
        patch("app.routers.health.get_pool", return_value=_pool_que(1)),
        patch("app.routers.health.get_redis", return_value=redis),
    ):
        respuesta = await _get("/health/ready")

    assert respuesta.status_code == 200
    assert respuesta.json()["checks"] == {"database": "ok", "redis": "ok"}
    assert redis.ping.await_count == 1


async def test_ready_devuelve_503_con_la_base_de_datos_caida() -> None:
    """Este es el caso que `/health` reportaba como «ok» indefinidamente."""
    with (
        patch("app.routers.health.get_pool", return_value=_pool_que(error=OSError("sin conexión"))),
        patch("app.routers.health.get_redis", return_value=AsyncMock()),
    ):
        respuesta = await _get("/health/ready")

    assert respuesta.status_code == 503
    assert respuesta.json()["status"] == "not_ready"
    assert respuesta.json()["checks"]["database"] == "error"


async def test_ready_no_publica_el_motivo_del_fallo() -> None:
    """El texto de una excepción de red trae el destino: `postgres:5432`."""
    fallo = OSError("connect failed: postgres:5432 (Name or service not known)")
    with (
        patch("app.routers.health.get_pool", return_value=_pool_que(error=fallo)),
        patch("app.routers.health.get_redis", return_value=AsyncMock()),
    ):
        cuerpo = (await _get("/health/ready")).text

    assert "postgres:5432" not in cuerpo
    assert "Name or service not known" not in cuerpo


async def test_ready_informa_el_consumidor_pero_no_lo_hace_bloqueante() -> None:
    """El consumidor no impide atender peticiones, pero un consumidor muerto
    tiene que VERSE en algún sitio — ese era el problema de fondo."""
    from orpycamcp_common import events

    events._estado_consumidores["signature"] = {
        "corriendo": False,
        "reinicios": 3,
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

    assert respuesta.status_code == 200, "el consumidor no decide el 503"
    assert respuesta.json()["checks"]["consumer"] == "reiniciando"
