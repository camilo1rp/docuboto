# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del stream worker de notification-service (E16).

Cubre el comportamiento del bucle externo de run():
  - La expiración normal de block= (RedisTimeoutError) no loguea WARNING ni hace sleep.
  - El bucle reanuda de inmediato tras el timeout.
  - El xack y el despacho a handlers se mantienen intactos.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import redis.exceptions as redis_exc


@pytest.mark.asyncio
async def test_run_timeout_es_silencioso_y_reanuda_escucha() -> None:
    """RedisTimeoutError en xreadgroup (block= expirado) no loguea WARNING ni hace sleep.

    El timeout de block=5000 ms es comportamiento normal de redis-py 8.x async cuando
    no hay entradas nuevas en los streams.  El bucle debe continuar de inmediato, sin
    registrar ningún mensaje de error y sin backoff innecesario.
    """
    from app.workers import stream_worker

    fake_redis = MagicMock()
    fake_redis.xreadgroup = AsyncMock(
        side_effect=[
            redis_exc.TimeoutError("Timeout reading from redis:6379"),
            asyncio.CancelledError(),
        ]
    )
    fake_redis.xgroup_create = AsyncMock()

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(stream_worker, "logger") as mock_logger,
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    # El bucle se reanudó: xreadgroup fue invocado dos veces.
    assert fake_redis.xreadgroup.call_count == 2

    # Ningún WARNING del bucle general fue emitido por la expiración normal.
    for call in mock_logger.warning.call_args_list:
        assert "Error en el bucle" not in (call.args[0] if call.args else "")

    # No se aplicó backoff (asyncio.sleep no fue llamado por la rama del timeout).
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_run_despacha_a_handler_y_ackea() -> None:
    """Los mensajes recibidos se despachan al handler correcto y se ackean.

    Verifica que el refactor del bucle externo no alteró la semántica de despacho
    por stream ni el xack incondicional por mensaje.
    """
    from app.workers import stream_worker

    fake_redis = MagicMock()
    msg_id = b"1700000000000-0"
    msg_data: dict = {"key": "value"}
    stream_name = stream_worker.WORKFLOW_STREAM.encode()

    fake_redis.xreadgroup = AsyncMock(
        side_effect=[
            [(stream_name, [(msg_id, msg_data)])],
            asyncio.CancelledError(),
        ]
    )
    fake_redis.xack = AsyncMock()

    mock_handler = AsyncMock()

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch.dict(stream_worker._HANDLERS, {stream_worker.WORKFLOW_STREAM: mock_handler}),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    mock_handler.assert_called_once_with(msg_data)
    fake_redis.xack.assert_called_once_with(
        stream_worker.WORKFLOW_STREAM, stream_worker.GROUP, msg_id
    )
