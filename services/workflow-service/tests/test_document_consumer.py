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
  - Semántica A-1 (R-WF1): xack ocurre SOLO si _handle tiene éxito; un fallo
    deja el mensaje en la PEL sin ackear para que reclaim_stale lo reintente.
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
async def test_run_xack_solo_en_exito() -> None:
    """xack se llama cuando _handle tiene éxito — semántica A-1 (R-WF1).

    Cuando el handler procesa el mensaje sin lanzar excepción, el bucle debe
    acusar recibo (xack) para liberar el mensaje de la PEL.
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
            AsyncMock(),  # éxito — no lanza
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    # xack se llama exactamente una vez en el caso de éxito.
    fake_redis.xack.assert_called_once_with(
        document_consumer.DOCUMENT_STREAM, document_consumer.GROUP, msg_id
    )


@pytest.mark.asyncio
async def test_run_reclaim_invocado_despues_del_intervalo() -> None:
    """run() llama events.reclaim_stale cuando RECLAIM_INTERVAL ha transcurrido."""
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_reclaim = AsyncMock(return_value=None)

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch("app.workers.document_consumer.events.reclaim_stale", mock_reclaim),
        patch("app.workers.document_consumer.time") as mock_time,
    ):
        mock_time.monotonic.side_effect = [0.0, document_consumer.RECLAIM_INTERVAL + 1.0]
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    mock_reclaim.assert_awaited_once()
    assert mock_reclaim.await_args.kwargs["deadletter_stream"] == document_consumer.DEADLETTER_STREAM


@pytest.mark.asyncio
async def test_run_xack_no_se_llama_si_handle_falla() -> None:
    """xack NO se llama cuando _handle lanza — mensaje queda en PEL para reintento.

    Semántica A-1 (R-WF1 / ADR-021): un fallo del handler de distribución deja
    el mensaje en la Pending Entries List.  El barrido reclaim_stale lo recogerá
    y, tras max_deliveries, lo escalará al dead-letter stream con traza durable.
    El bucle debe continuar (no crashear) y salir limpiamente con CancelledError.
    """
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    msg_id = b"1700000000000-0"
    msg_data: dict = {}

    # Primera llamada entrega un mensaje cuyo handler fallará;
    # la segunda detiene el bucle.
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

    # xack NO debe haberse llamado — el mensaje queda en la PEL.
    fake_redis.xack.assert_not_called()


# ===========================================================================
# DLQ trim gate (ADR-021 Fase 4 — MVP-2)
# ===========================================================================


@pytest.mark.asyncio
async def test_run_invoca_trim_dlq_despues_del_intervalo() -> None:
    """events.trim_deadletter es llamado con DEADLETTER_STREAM y max_age_ms correcto
    cuando DLQ_TRIM_INTERVAL ha transcurrido.
    """
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_trim = AsyncMock(return_value=0)

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch("app.workers.document_consumer.events.reclaim_stale", AsyncMock()),
        patch("app.workers.document_consumer.events.trim_deadletter", mock_trim),
        patch("app.workers.document_consumer.time") as mock_time,
    ):
        mock_time.monotonic.side_effect = [
            0.0,
            document_consumer.DLQ_TRIM_INTERVAL + 1.0,
        ]
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    mock_trim.assert_awaited_once()
    call = mock_trim.await_args
    assert call.args[1] == document_consumer.DEADLETTER_STREAM
    # max_age_ms must equal retention_dlq_days (default 30) × 86 400 000 ms/day.
    assert call.kwargs["max_age_ms"] == 30 * 86_400_000


@pytest.mark.asyncio
async def test_run_no_invoca_trim_dlq_antes_del_intervalo() -> None:
    """events.trim_deadletter NO se llama si DLQ_TRIM_INTERVAL aún no ha transcurrido."""
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_trim = AsyncMock(return_value=0)

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch("app.workers.document_consumer.events.reclaim_stale", AsyncMock()),
        patch("app.workers.document_consumer.events.trim_deadletter", mock_trim),
        patch("app.workers.document_consumer.time") as mock_time,
    ):
        mock_time.monotonic.side_effect = [0.0, 5.0]
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    mock_trim.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_fallo_en_trim_dlq_no_tumba_el_loop() -> None:
    """Un error en trim_deadletter se registra como WARNING; el loop no crashea."""
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch("app.workers.document_consumer.events.reclaim_stale", AsyncMock()),
        patch(
            "app.workers.document_consumer.events.trim_deadletter",
            AsyncMock(side_effect=RuntimeError("redis conn error")),
        ),
        patch("app.workers.document_consumer.time") as mock_time,
        patch.object(document_consumer, "logger") as mock_logger,
    ):
        mock_time.monotonic.side_effect = [
            0.0,
            document_consumer.DLQ_TRIM_INTERVAL + 1.0,
        ]
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    assert fake_redis.xreadgroup.call_count == 2
    warning_messages = [
        call.args[0] for call in mock_logger.warning.call_args_list if call.args
    ]
    assert any("trim" in msg.lower() or "DLQ" in msg for msg in warning_messages)
