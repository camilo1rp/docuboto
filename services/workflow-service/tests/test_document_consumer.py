# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del consumidor de eventos de documentos en workflow-service (E05).

Cubre el comportamiento del bucle externo de run():
  - La expiración normal de block= (RedisTimeoutError) no loguea WARNING ni hace sleep.
  - El bucle reanuda de inmediato tras el timeout.
  - El xack incondicional (ack siempre, incluso si _handle lanza) no se altera.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import redis.exceptions as redis_exc


@pytest.mark.asyncio
async def test_run_timeout_es_silencioso_y_reanuda_escucha() -> None:
    """RedisTimeoutError en xreadgroup (block= expirado) no loguea WARNING ni hace sleep.

    El timeout de block=5000 ms es comportamiento normal de redis-py 8.x async cuando
    no hay entradas nuevas en el stream.  El bucle debe continuar de inmediato, sin
    registrar ningún mensaje de error y sin backoff innecesario.
    """
    from app.workers import document_consumer

    fake_redis = AsyncMock()

    # Primera llamada: expiración normal del block → TimeoutError.
    # Segunda llamada: CancelledError para salir del bucle.
    fake_redis.xreadgroup.side_effect = [
        redis_exc.TimeoutError("Timeout reading from redis:6379"),
        asyncio.CancelledError(),
    ]

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(document_consumer, "logger") as mock_logger,
    ):
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    # El bucle se reanudó: xreadgroup fue invocado dos veces.
    assert fake_redis.xreadgroup.call_count == 2

    # Ningún WARNING del bucle general fue emitido por la expiración normal.
    for call in mock_logger.warning.call_args_list:
        assert "Error en el bucle" not in (call.args[0] if call.args else "")

    # No se aplicó backoff (asyncio.sleep no fue llamado por la rama del timeout).
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_run_xack_es_incondicional() -> None:
    """xack se llama aunque _handle lance — la semántica at-most-once del workflow no cambia.

    El workflow-service ackea siempre (a diferencia del signature-service que ackea
    solo en éxito).  Verificamos que el fix del bucle externo no alteró esa semántica.
    """
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    msg_id = b"1700000000000-0"
    msg_data: dict = {}

    # Primera llamada entrega un mensaje; la segunda detiene el bucle.
    fake_redis.xreadgroup.side_effect = [
        [(document_consumer.DOCUMENT_STREAM, [(msg_id, msg_data)])],
        asyncio.CancelledError(),
    ]

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch.object(
            document_consumer,
            "_handle",
            AsyncMock(side_effect=RuntimeError("fallo transitorio")),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    # xack se llamó a pesar del fallo de _handle.
    fake_redis.xack.assert_called_once_with(
        document_consumer.DOCUMENT_STREAM, document_consumer.GROUP, msg_id
    )
