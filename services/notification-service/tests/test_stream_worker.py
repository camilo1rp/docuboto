# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del stream worker de notification-service (E16, ADR-021 Fase 3).

Cubre:
  1. Timeout silencioso de block= → no loguea WARNING ni hace sleep (comportamiento
     de redis-py 8.x cuando el block= expira sin entradas nuevas).
  2. A-1 (ack-only-on-success): xack se llama si el handler tiene éxito.
  3. A-1: xack NO se llama si el handler lanza excepción; el mensaje queda en PEL.
  4. Barrido reclaim_stale se invoca una vez por stream cuando RECLAIM_INTERVAL
     ha transcurrido.
  5. Un fallo en el barrido no tumba el loop (loguea WARNING y continúa).
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
        patch("app.workers.stream_worker.time") as mock_time,
        patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(stream_worker, "logger") as mock_logger,
    ):
        # Prevent the reclaim sweep from firing during this test.
        mock_time.monotonic.return_value = 0.0
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
async def test_run_xack_si_handler_tiene_exito() -> None:
    """A-1: xack se llama cuando el handler tiene éxito (no lanza).

    El refactor del bucle externo mantiene la semántica de despacho por stream
    y el xack condicional (A-1) cuando el handler completa sin excepción.
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

    mock_handler = AsyncMock()  # success — does not raise

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.time") as mock_time,
        patch.dict(stream_worker._HANDLERS, {stream_worker.WORKFLOW_STREAM: mock_handler}),
    ):
        mock_time.monotonic.return_value = 0.0  # no reclaim sweep
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    mock_handler.assert_called_once_with(msg_data)
    fake_redis.xack.assert_called_once_with(
        stream_worker.WORKFLOW_STREAM, stream_worker.GROUP, msg_id
    )


@pytest.mark.asyncio
async def test_run_xack_no_se_llama_si_handler_falla() -> None:
    """A-1: xack NO se llama cuando el handler lanza — mensaje queda en PEL.

    Semántica A-1 (ADR-021 Fase 3): un fallo del handler deja el mensaje en la
    Pending Entries List.  El barrido reclaim_stale lo recogerá y, tras
    max_deliveries=5, lo escalará al dead-letter stream con traza durable.
    El loop debe continuar (no crashear) y salir limpiamente con CancelledError.
    """
    from app.workers import stream_worker

    fake_redis = MagicMock()
    msg_id = b"1700000000000-0"
    msg_data: dict = {"event_id": "evt-001", "tenant_slug": "demo"}
    stream_name = stream_worker.WORKFLOW_STREAM.encode()

    fake_redis.xreadgroup = AsyncMock(
        side_effect=[
            [(stream_name, [(msg_id, msg_data)])],
            asyncio.CancelledError(),
        ]
    )
    fake_redis.xack = AsyncMock()

    mock_handler = AsyncMock(side_effect=ValueError("dedup BD caída"))

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.time") as mock_time,
        patch.dict(stream_worker._HANDLERS, {stream_worker.WORKFLOW_STREAM: mock_handler}),
    ):
        mock_time.monotonic.return_value = 0.0  # no reclaim sweep
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    # A-1: xack must NOT have been called — message stays in the PEL.
    fake_redis.xack.assert_not_called()


@pytest.mark.asyncio
async def test_run_reclaim_llamado_por_cada_stream() -> None:
    """reclaim_stale se invoca una vez por stream cuando RECLAIM_INTERVAL ha transcurrido.

    El barrido de cadencia debe llamar a events.reclaim_stale para cada uno de los
    dos streams (WORKFLOW_STREAM y DOCUMENT_STREAM), pasando el handler correspondiente
    y el mismo DEADLETTER_STREAM.
    """
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],  # no messages in first iteration
        asyncio.CancelledError(),
    ]

    mock_reclaim = AsyncMock(return_value=None)
    mock_time = MagicMock()
    # Call 1: before loop → 0.0 (_last_reclaim); Call 2: first iteration → 31.0 (triggers sweep)
    mock_time.monotonic.side_effect = [0.0, stream_worker.RECLAIM_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", mock_reclaim),
        patch("app.workers.stream_worker.time", mock_time),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    # reclaim_stale must be called once per stream (two streams).
    assert mock_reclaim.call_count == 2
    called_streams = {call.args[1] for call in mock_reclaim.call_args_list}
    assert called_streams == {stream_worker.WORKFLOW_STREAM, stream_worker.DOCUMENT_STREAM}

    # Each call must pass the unified dead-letter stream.
    for call in mock_reclaim.call_args_list:
        assert call.kwargs.get("deadletter_stream") == stream_worker.DEADLETTER_STREAM
        assert call.kwargs.get("on_dead_letter") is stream_worker._persist_dead_letter


@pytest.mark.asyncio
async def test_run_trim_dlq_llamado_junto_con_retention() -> None:
    """MVP-2: events.trim_deadletter se llama en el mismo gate diario que retention.run_once.

    Verifica que ambas tareas se disparan cuando RETENTION_INTERVAL ha transcurrido,
    y que trim_deadletter recibe DEADLETTER_STREAM y el max_age_ms correcto
    (retention_dlq_days * 86 400 000).
    """
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_reclaim = AsyncMock()
    mock_run_once = AsyncMock()
    mock_trim = AsyncMock(return_value=0)
    mock_time = MagicMock()
    mock_time.monotonic.side_effect = [0.0, stream_worker.RETENTION_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", mock_reclaim),
        patch("app.workers.stream_worker.retention.run_once", mock_run_once),
        patch("app.workers.stream_worker.events.trim_deadletter", mock_trim),
        patch("app.workers.stream_worker.time", mock_time),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    # Both dedup purge and DLQ trim must fire in the same gate.
    mock_run_once.assert_awaited_once()
    mock_trim.assert_awaited_once()

    call = mock_trim.await_args
    assert call.args[1] == stream_worker.DEADLETTER_STREAM
    # max_age_ms = retention_dlq_days (default 30) × 86 400 000 ms/day.
    assert call.kwargs["max_age_ms"] == 30 * 86_400_000


@pytest.mark.asyncio
async def test_run_trim_dlq_no_llamado_antes_del_intervalo() -> None:
    """MVP-2: trim_deadletter NO se llama si RETENTION_INTERVAL aún no ha transcurrido."""
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_trim = AsyncMock(return_value=0)
    mock_time = MagicMock()
    mock_time.monotonic.return_value = 0.0  # never crosses the interval

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.trim_deadletter", mock_trim),
        patch("app.workers.stream_worker.time", mock_time),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    mock_trim.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_fallo_trim_dlq_no_tumba_el_loop() -> None:
    """MVP-2: un error en trim_deadletter se registra como WARNING; el loop continúa.

    retention.run_once completa con éxito; trim_deadletter falla; el loop sale
    limpiamente via CancelledError.
    """
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_time = MagicMock()
    mock_time.monotonic.side_effect = [0.0, stream_worker.RETENTION_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", AsyncMock()),
        patch("app.workers.stream_worker.retention.run_once", AsyncMock()),
        patch(
            "app.workers.stream_worker.events.trim_deadletter",
            AsyncMock(side_effect=RuntimeError("redis timeout")),
        ),
        patch("app.workers.stream_worker.time", mock_time),
        patch.object(stream_worker, "logger") as mock_logger,
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    # Loop survived.
    assert fake_redis.xreadgroup.call_count == 2
    warning_messages = [
        call.args[0] for call in mock_logger.warning.call_args_list if call.args
    ]
    assert any("trim" in msg.lower() or "DLQ" in msg for msg in warning_messages)


@pytest.mark.asyncio
async def test_run_fallo_en_barrido_no_tumba_el_loop() -> None:
    """Un fallo en reclaim_stale se registra como WARNING y el loop continúa."""
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [
        [],
        asyncio.CancelledError(),
    ]

    mock_time = MagicMock()
    mock_time.monotonic.side_effect = [0.0, stream_worker.RECLAIM_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch(
            "app.workers.stream_worker.events.reclaim_stale",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ),
        patch("app.workers.stream_worker.time", mock_time),
        patch.object(stream_worker, "logger") as mock_logger,
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    # The loop survived and exited cleanly via CancelledError (not RuntimeError).
    # A warning must have been logged for the sweep failure.
    warning_messages = [
        call.args[0] for call in mock_logger.warning.call_args_list if call.args
    ]
    assert any("barrido" in msg for msg in warning_messages)


@pytest.mark.asyncio
async def test_los_streams_de_DOMINIO_tambien_se_recortan() -> None:
    """Los de dead-letter ya caducaban; los de dominio no los recortaba nadie.

    Y son los que llevan el contenido: el asunto y el nombre del ciudadano de
    cada radicación quedaban en claro y sin caducar, fuera del schema de la
    entidad y fuera de la Tabla de Retención Documental.

    Probar la función de recorte aislada no prueba que se invoque: esto mira el
    ciclo real, y comprueba además CUÁLES streams recibe.
    """
    from app.workers import stream_worker

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [[], asyncio.CancelledError()]

    mock_trim_dominio = AsyncMock(return_value=0)
    mock_time = MagicMock()
    mock_time.monotonic.side_effect = [0.0, stream_worker.RETENTION_INTERVAL + 1.0]

    with (
        patch.object(stream_worker, "get_redis", return_value=fake_redis),
        patch("app.workers.stream_worker.events.ensure_group", AsyncMock()),
        patch("app.workers.stream_worker.events.reclaim_stale", AsyncMock()),
        patch("app.workers.stream_worker.retention.run_once", AsyncMock()),
        patch("app.workers.stream_worker.events.trim_deadletter", AsyncMock(return_value=0)),
        patch("app.workers.stream_worker.events.trim_stream", mock_trim_dominio),
        patch("app.workers.stream_worker.time", mock_time),
    ):
        with pytest.raises(asyncio.CancelledError):
            await stream_worker.run()

    recortados = {c.args[1] for c in mock_trim_dominio.await_args_list}
    assert stream_worker.DOCUMENT_STREAM in recortados, (
        "el stream que lleva el asunto y el ciudadano de cada radicación no caduca"
    )
    assert stream_worker.WORKFLOW_STREAM in recortados
    for c in mock_trim_dominio.await_args_list:
        assert c.kwargs["max_age_ms"] == 30 * 86_400_000
