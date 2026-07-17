# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del consumidor de eventos de documentos en knowledge-service (E21).

Cubre el comportamiento del bucle externo de run() (espejo de
workflow-service/tests/test_document_consumer.py) y el despacho de _handle():
  - La expiración normal de block= (RedisTimeoutError) no loguea WARNING ni hace sleep.
  - xack ocurre SOLO si _handle tiene éxito; un fallo deja el mensaje en la PEL.
  - El barrido de reclamación se invoca tras RECLAIM_INTERVAL.
  - _handle despacha created→ingest_radicado_created, reclassified→apply_reclassification,
    otros event_type→ignora, y eventos sin tenant/tracking_number→ignora (sin tocar BD).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import redis.exceptions as redis_exc
from orpycamcp_common import events

TENANT = "demo"
TRACKING = "2026-DEMO-E-000001"


class _AcquireTenantCtx:
    """asynccontextmanager de una sola conexión, para simular acquire_tenant."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _fields(event_type: str, *, tenant_slug: str = TENANT, payload: dict | None = None) -> dict:
    envelope = events.build_event(event_type, tenant_slug=tenant_slug, payload=payload or {})
    return events.to_fields(envelope)


# ===========================================================================
# run() — bucle externo (espejo de workflow-service)
# ===========================================================================


@pytest.mark.asyncio
async def test_run_timeout_es_silencioso_y_reanuda_escucha() -> None:
    from app.workers import document_consumer

    fake_redis = AsyncMock()
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

    assert fake_redis.xreadgroup.call_count == 2
    for call in mock_logger.warning.call_args_list:
        assert "Error en el bucle" not in (call.args[0] if call.args else "")
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_run_xack_solo_en_exito() -> None:
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    msg_id = b"1700000000000-0"
    msg_data: dict = {}
    fake_redis.xreadgroup.side_effect = [
        [(document_consumer.DOCUMENT_STREAM, [(msg_id, msg_data)])],
        asyncio.CancelledError(),
    ]

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch.object(document_consumer, "_handle", AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    fake_redis.xack.assert_called_once_with(
        document_consumer.DOCUMENT_STREAM, document_consumer.GROUP, msg_id
    )


@pytest.mark.asyncio
async def test_run_xack_no_se_llama_si_handle_falla() -> None:
    """Un fallo de embed/BD dentro de _handle deja el mensaje en la PEL (sin XACK).

    El siguiente barrido de reclamación lo reintentará y, tras max_deliveries,
    lo escalará al dead-letter stream (ver test_dead_letter.py).
    """
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    msg_id = b"1700000000000-0"
    msg_data: dict = {}
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
            AsyncMock(side_effect=RuntimeError("fallo de embedding/BD")),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    fake_redis.xack.assert_not_called()


@pytest.mark.asyncio
async def test_run_reclaim_invocado_despues_del_intervalo() -> None:
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [[], asyncio.CancelledError()]
    mock_reclaim = AsyncMock(return_value=events.ReclaimStats())

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
    kw = mock_reclaim.await_args.kwargs
    assert kw["deadletter_stream"] == document_consumer.DEADLETTER_STREAM
    assert kw["on_dead_letter"] is document_consumer._persist_dead_letter


@pytest.mark.asyncio
async def test_run_no_invoca_reclaim_antes_del_intervalo() -> None:
    from app.workers import document_consumer

    fake_redis = AsyncMock()
    fake_redis.xreadgroup.side_effect = [[], asyncio.CancelledError()]
    mock_reclaim = AsyncMock(return_value=events.ReclaimStats())

    with (
        patch.object(document_consumer, "get_redis", return_value=fake_redis),
        patch("app.workers.document_consumer.events.ensure_group", AsyncMock()),
        patch("app.workers.document_consumer.events.reclaim_stale", mock_reclaim),
        patch("app.workers.document_consumer.time") as mock_time,
    ):
        mock_time.monotonic.side_effect = [0.0, 5.0]
        with pytest.raises(asyncio.CancelledError):
            await document_consumer.run()

    mock_reclaim.assert_not_awaited()


# ===========================================================================
# _handle — despacho por event_type
# ===========================================================================


@pytest.mark.asyncio
async def test_handle_dispatches_created_to_ingest() -> None:
    from app.workers import document_consumer

    fields = _fields(
        "document.radicado.created",
        payload={"tracking_number": TRACKING, "nivel_seguridad": 1},
    )
    fake_conn = AsyncMock()

    with (
        patch.object(document_consumer, "acquire_tenant", return_value=_AcquireTenantCtx(fake_conn)),
        patch(
            "app.workers.document_consumer.ingestion.ingest_radicado_created", AsyncMock()
        ) as mock_ingest,
    ):
        await document_consumer._handle(fields)

    mock_ingest.assert_awaited_once()
    kw = mock_ingest.await_args.kwargs
    assert kw["tenant_slug"] == TENANT
    assert kw["payload"]["tracking_number"] == TRACKING


@pytest.mark.asyncio
async def test_handle_dispatches_reclassified_to_apply() -> None:
    from app.workers import document_consumer

    fields = _fields(
        "document.radicado.reclassified",
        payload={"tracking_number": TRACKING, "nivel_nuevo": 2, "nivel_anterior": 1},
    )
    fake_conn = AsyncMock()

    with (
        patch.object(document_consumer, "acquire_tenant", return_value=_AcquireTenantCtx(fake_conn)),
        patch(
            "app.workers.document_consumer.ingestion.apply_reclassification", AsyncMock()
        ) as mock_apply,
    ):
        await document_consumer._handle(fields)

    mock_apply.assert_awaited_once()
    kw = mock_apply.await_args.kwargs
    assert kw["tenant_slug"] == TENANT
    assert kw["payload"]["nivel_nuevo"] == 2


@pytest.mark.asyncio
async def test_handle_dispatches_annulled_to_void() -> None:
    """Residual "purga-en-anulación" (E21): `document.radicado.annulled` despacha
    a `ingestion.void_radicado`, NUNCA a `ingest_radicado_created` ni a
    `apply_reclassification` — es su propio handler, no un caso más del `else`
    genérico que antes solo cubría `reclassified`."""
    from app.workers import document_consumer

    fields = _fields(
        "document.radicado.annulled",
        payload={"id": "doc-id-1", "tracking_number": TRACKING},
    )
    fake_conn = AsyncMock()

    with (
        patch.object(document_consumer, "acquire_tenant", return_value=_AcquireTenantCtx(fake_conn)),
        patch(
            "app.workers.document_consumer.ingestion.void_radicado", AsyncMock()
        ) as mock_void,
        patch(
            "app.workers.document_consumer.ingestion.ingest_radicado_created", AsyncMock()
        ) as mock_ingest,
        patch(
            "app.workers.document_consumer.ingestion.apply_reclassification", AsyncMock()
        ) as mock_apply,
    ):
        await document_consumer._handle(fields)

    mock_void.assert_awaited_once()
    kw = mock_void.await_args.kwargs
    assert kw["tenant_slug"] == TENANT
    assert kw["payload"]["tracking_number"] == TRACKING
    mock_ingest.assert_not_called()
    mock_apply.assert_not_called()


@pytest.mark.asyncio
async def test_handle_ignores_unrelated_event_types() -> None:
    """Eventos que no son created/reclassified se ignoran (return → XACK normal)."""
    from app.workers import document_consumer

    fields = _fields("document.radicado.updated", payload={"tracking_number": TRACKING})

    with patch.object(document_consumer, "acquire_tenant") as mock_acquire:
        await document_consumer._handle(fields)

    mock_acquire.assert_not_called()


@pytest.mark.asyncio
async def test_handle_ignores_event_without_tenant_slug() -> None:
    """#2: sin `tenant_slug`, `_handle` NO llama a `ingestion` (ni a `acquire_tenant`
    para el despacho de negocio), pero SÍ deja traza bajo SYSTEM_TENANT ANTES del
    `return` — ya no es un drop silencioso (hallazgo tenant-security-auditor)."""
    from app.workers import document_consumer

    fields = _fields(
        "document.radicado.created", tenant_slug="", payload={"tracking_number": TRACKING}
    )
    fake_pool_conn = AsyncMock()

    with (
        patch.object(document_consumer, "acquire_tenant") as mock_acquire,
        patch.object(document_consumer, "get_pool") as mock_get_pool,
        patch.object(document_consumer, "_audit") as mock_audit,
        patch.object(document_consumer, "_AUDIT_AVAILABLE", True),
        patch.object(document_consumer, "ingestion") as mock_ingestion,
    ):
        mock_get_pool.return_value.acquire.return_value = _AcquireTenantCtx(fake_pool_conn)
        mock_audit.append = AsyncMock()
        await document_consumer._handle(fields)

    mock_acquire.assert_not_called()
    mock_ingestion.ingest_radicado_created.assert_not_called()
    mock_ingestion.apply_reclassification.assert_not_called()
    mock_audit.append.assert_awaited_once()
    kw = mock_audit.append.call_args.kwargs
    assert kw["tenant_slug"] is None  # → SYSTEM_TENANT
    assert kw["action"] == "knowledge.ingest_malformed_event"
    assert kw["payload"]["tracking_number"] == TRACKING


@pytest.mark.asyncio
async def test_handle_ignores_event_without_tracking_number() -> None:
    """#2: sin `tracking_number` pero CON `tenant_slug`, `_handle` no despacha a
    `ingestion`, pero SÍ deja traza bajo ese tenant (usa `acquire_tenant` para
    escribir la traza, no para el despacho de negocio)."""
    from app.workers import document_consumer

    fields = _fields("document.radicado.created", payload={})
    fake_conn = AsyncMock()

    with (
        patch.object(
            document_consumer, "acquire_tenant", return_value=_AcquireTenantCtx(fake_conn)
        ) as mock_acquire,
        patch.object(document_consumer, "_audit") as mock_audit,
        patch.object(document_consumer, "_AUDIT_AVAILABLE", True),
        patch.object(document_consumer, "ingestion") as mock_ingestion,
    ):
        mock_audit.append = AsyncMock()
        await document_consumer._handle(fields)

    mock_acquire.assert_called_once_with(TENANT)
    mock_ingestion.ingest_radicado_created.assert_not_called()
    mock_ingestion.apply_reclassification.assert_not_called()
    mock_audit.append.assert_awaited_once()
    kw = mock_audit.append.call_args.kwargs
    assert kw["tenant_slug"] == TENANT
    assert kw["action"] == "knowledge.ingest_malformed_event"
    assert kw["payload"]["tenant_slug"] == TENANT
    assert kw["payload"]["tracking_number"] is None


@pytest.mark.asyncio
async def test_handle_malformed_event_trace_is_best_effort() -> None:
    """Un fallo AL AUDITAR la traza de un evento malformado no debe propagar —
    best-effort a propósito (reintentar un evento malformado no lo arregla)."""
    from app.workers import document_consumer

    fields = _fields(
        "document.radicado.created", tenant_slug="", payload={"tracking_number": TRACKING}
    )

    with (
        patch.object(document_consumer, "get_pool") as mock_get_pool,
        patch.object(document_consumer, "_audit") as mock_audit,
        patch.object(document_consumer, "_AUDIT_AVAILABLE", True),
    ):
        mock_get_pool.return_value.acquire.side_effect = RuntimeError("pool exhausted")
        mock_audit.append = AsyncMock()
        await document_consumer._handle(fields)  # no debe propagar
