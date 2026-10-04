# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-13-12: `/health/ready` muestra el tamaño de la DLQ y el último barrido de
reclamación de workflow-service. Antes el `ReclaimStats` se descartaba y la PEL/DLQ no se veía."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from orpycamcp_common import events

from app.main import app

pytestmark = pytest.mark.asyncio

_NOMBRE = "workflow"


def _pool() -> MagicMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    pool = MagicMock()
    pool.acquire.return_value = AsyncMock(
        __aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)
    )
    return pool


async def _ready(xlen: int, estado: dict) -> tuple[int, dict]:
    redis = AsyncMock()
    redis.xlen.return_value = xlen
    events._estado_consumidores[_NOMBRE] = {
        "corriendo": True, "reinicios": 0, "ultimo_error": None, **estado,
    }
    try:
        with (
            patch("app.routers.health.get_pool", return_value=_pool()),
            patch("app.routers.health.get_redis", return_value=redis),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.get("/health/ready")
    finally:
        events._estado_consumidores.pop(_NOMBRE, None)
    return r.status_code, r.json()


async def test_una_dlq_con_entradas_degrada_sin_tumbar_la_sonda() -> None:
    codigo, cuerpo = await _ready(3, {"dead_lettered_total": 3, "ultimo_barrido": "t"})
    assert codigo == 200, "el consumidor informa, no decide el 503"
    assert cuerpo["checks"]["dead_letter"] == "degraded"
    assert cuerpo["consumer"]["dead_letter_stream_len"] == 3
    assert cuerpo["consumer"]["dead_lettered_total"] == 3


async def test_una_dlq_vacia_queda_ok() -> None:
    _, cuerpo = await _ready(0, {})
    assert cuerpo["checks"]["dead_letter"] == "ok"
    assert cuerpo["consumer"]["dead_letter_stream_len"] == 0
