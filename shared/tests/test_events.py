# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de orpycamcp_common.events (sobre canónico; sin Redis real)."""

from unittest.mock import AsyncMock, call

import pytest
from orpycamcp_common import events


def test_build_event_has_canonical_fields() -> None:
    ev = events.build_event(
        "radicado.creado",
        tenant_slug="icetex",
        payload={"tracking_number": "2024-ICETEX-E-001234"},
        actor="user-1",
    )
    for key in (
        "event_id",
        "event_type",
        "version",
        "tenant_slug",
        "occurred_at",
        "actor",
        "correlation_id",
        "dedup_key",
        "payload",
    ):
        assert key in ev
    assert ev["event_type"] == "radicado.creado"
    assert ev["version"] == 1
    assert ev["tenant_slug"] == "icetex"
    assert ev["payload"]["tracking_number"] == "2024-ICETEX-E-001234"


def test_fields_roundtrip() -> None:
    ev = events.build_event(
        "radicado.anulado",
        tenant_slug="icetex",
        payload={"motivo": "duplicado", "n": 3},
        actor="user-9",
        dedup_key="radicado.anulado:abc",
    )
    parsed = events.parse_fields(events.to_fields(ev))
    assert parsed["event_id"] == ev["event_id"]
    assert parsed["event_type"] == ev["event_type"]
    assert parsed["version"] == ev["version"]
    assert parsed["tenant_slug"] == ev["tenant_slug"]
    assert parsed["actor"] == ev["actor"]
    assert parsed["dedup_key"] == ev["dedup_key"]
    assert parsed["payload"] == ev["payload"]


def test_parse_fields_tolerates_bytes() -> None:
    ev = events.build_event("x.y", tenant_slug="t", payload={"a": 1})
    raw = {k.encode(): v.encode() for k, v in events.to_fields(ev).items()}
    parsed = events.parse_fields(raw)
    assert parsed["tenant_slug"] == "t"
    assert parsed["payload"] == {"a": 1}


@pytest.mark.asyncio
async def test_publish_calls_xadd() -> None:
    redis = AsyncMock()
    redis.xadd.return_value = "1-0"
    ev = events.build_event("radicado.creado", tenant_slug="icetex")
    msg_id = await events.publish(redis, "orpycamcp.document.events", ev)
    assert msg_id == "1-0"
    redis.xadd.assert_awaited_once()
    stream_arg, fields_arg = redis.xadd.await_args.args
    assert stream_arg == "orpycamcp.document.events"
    assert fields_arg["event_type"] == "radicado.creado"


@pytest.mark.asyncio
async def test_emit_builds_and_publishes() -> None:
    redis = AsyncMock()
    redis.xadd.return_value = "1-0"
    msg_id, ev = await events.emit(
        redis,
        "orpycamcp.workflow.events",
        "radicado.reasignado",
        tenant_slug="icetex",
        payload={"nueva_depe": "juridica"},
    )
    assert msg_id == "1-0"
    assert ev["event_type"] == "radicado.reasignado"
    assert ev["payload"]["nueva_depe"] == "juridica"


@pytest.mark.asyncio
async def test_ensure_group_ignores_busygroup() -> None:
    redis = AsyncMock()
    redis.xgroup_create.side_effect = Exception("BUSYGROUP Consumer Group name already exists")
    # No debe propagar el error si el grupo ya existe.
    await events.ensure_group(redis, "orpycamcp.document.events", "workflow-router")


@pytest.mark.asyncio
async def test_ensure_group_raises_other_errors() -> None:
    redis = AsyncMock()
    redis.xgroup_create.side_effect = Exception("WRONGTYPE")
    with pytest.raises(Exception, match="WRONGTYPE"):
        await events.ensure_group(redis, "s", "g")


# ===========================================================================
# reclaim_stale — unit tests with a fake-minimal Redis double
# ===========================================================================
#
# redis-py 8.x xpending_range returns list[dict] with keys:
#   message_id, consumer, time_since_delivered, times_delivered
# xclaim returns list[StreamEntry] = list[tuple[str|bytes, dict[str,str]|None]]
# When decode_responses=True all str; the helper uses parse_fields which
# tolerates both bytes and str.


def _make_redis(
    pending: list[dict],
    claimed: list,  # return value for xclaim
) -> AsyncMock:
    """Build a minimal Redis double for reclaim_stale tests."""
    fake = AsyncMock()
    fake.xpending_range.return_value = pending
    fake.xclaim.return_value = claimed
    fake.xadd.return_value = "1-0"
    fake.xack.return_value = 1
    return fake


def _pending_entry(msg_id: str, times_delivered: int) -> dict:
    return {
        "message_id": msg_id,
        "consumer": "old-consumer",
        "time_since_delivered": 65_000,
        "times_delivered": times_delivered,
    }


def _claimed_entry(msg_id: str, tenant: str = "demo") -> tuple:
    """A StreamEntry tuple as returned by redis-py xclaim."""
    ev = events.build_event("x.y", tenant_slug=tenant, payload={"k": "v"})
    fields = events.to_fields(ev)
    return (msg_id, fields)


@pytest.mark.asyncio
async def test_reclaim_stale_reprocesses_orphan_under_threshold() -> None:
    """Orphan with times_delivered < max_deliveries → claimed, handler called, XACK."""
    msg_id = "1680000000000-0"
    claimed_entry = _claimed_entry(msg_id)
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=2)],
        claimed=[claimed_entry],
    )
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
    )

    assert stats.reclaimed == 1
    assert stats.reprocessed_ok == 1
    assert stats.reprocessed_fail == 0
    assert stats.dead_lettered == 0

    handler.assert_awaited_once()
    # xack called once (on success)
    fake.xack.assert_awaited_once_with("stream", "grp", msg_id)
    # NOT routed to dead-letter
    fake.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_stale_dead_letters_poison_pill() -> None:
    """Poison pill with times_delivered >= max_deliveries → DL xadd, xack, handler NOT called."""
    msg_id = "1680000000001-0"
    claimed_entry = _claimed_entry(msg_id)
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=5)],
        claimed=[claimed_entry],
    )
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
    )

    assert stats.dead_lettered == 1
    assert stats.reclaimed == 0
    assert stats.reprocessed_ok == 0

    handler.assert_not_awaited()
    # Dead-letter stream received the event
    fake.xadd.assert_awaited_once()
    xadd_args = fake.xadd.await_args
    assert xadd_args.args[0] == "dl"
    dl_payload = xadd_args.args[1]
    assert dl_payload["dead_letter_reason"] == "max_deliveries_exceeded"
    assert dl_payload["dead_letter_delivery_count"] == "5"
    # Original stream acked after DL
    fake.xack.assert_awaited_once_with("stream", "grp", msg_id)


@pytest.mark.asyncio
async def test_reclaim_stale_failed_reprocess_does_not_ack() -> None:
    """handler raises → NO xack; message stays in PEL; reprocessed_fail=1."""
    msg_id = "1680000000002-0"
    claimed_entry = _claimed_entry(msg_id)
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=1)],
        claimed=[claimed_entry],
    )
    handler = AsyncMock(side_effect=RuntimeError("transient db error"))

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
    )

    assert stats.reprocessed_fail == 1
    assert stats.reprocessed_ok == 0
    fake.xack.assert_not_awaited()
    fake.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_stale_skips_when_xclaim_returns_empty() -> None:
    """Race: xclaim returns [] (already acked elsewhere) → silently ignored."""
    msg_id = "1680000000003-0"
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=3)],
        claimed=[],  # race: nothing to claim
    )
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
    )

    assert stats.reclaimed == 0
    assert stats.reprocessed_ok == 0
    assert stats.dead_lettered == 0
    handler.assert_not_awaited()
    fake.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_stale_race_on_dead_letter_path() -> None:
    """xclaim returns [] on the dead-letter path (another consumer acked first) → skipped."""
    msg_id = "1680000000004-0"
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=10)],  # above threshold
        claimed=[],
    )
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
    )

    assert stats.dead_lettered == 0
    fake.xadd.assert_not_awaited()
    fake.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_stale_on_dead_letter_callback_failure_blocks_ack() -> None:
    """R-4: on_dead_letter callback raises → NO xack; message stays in PEL; dead_lettered=0.

    The XADD to the dead-letter stream still happens (best-effort operational buffer)
    but the XACK is gated on the durable-persistence callback.  If the callback
    fails (e.g. Postgres down), the message remains in the PEL for the next sweep
    — there is no dead-letter without a durable trace (ADR-021 R-4).
    """
    msg_id = "1680000000005-0"
    claimed_entry = _claimed_entry(msg_id)
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=5)],
        claimed=[claimed_entry],
    )
    failing_callback = AsyncMock(side_effect=RuntimeError("postgres down"))
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
        on_dead_letter=failing_callback,
    )

    # Callback failed → not committed → dead_lettered counter NOT incremented.
    assert stats.dead_lettered == 0
    # XACK must NOT have been called — message stays in PEL for retry.
    fake.xack.assert_not_awaited()
    # XADD to DL stream still happened (best-effort buffer, before callback).
    fake.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_reclaim_stale_on_dead_letter_callback_ok_then_xack() -> None:
    """R-4: on_dead_letter callback succeeds → XACK occurs, dead_lettered=1."""
    msg_id = "1680000000005b-0"
    claimed_entry = _claimed_entry(msg_id)
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=5)],
        claimed=[claimed_entry],
    )
    ok_callback = AsyncMock()  # succeeds
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
        on_dead_letter=ok_callback,
    )

    assert stats.dead_lettered == 1
    fake.xack.assert_awaited_once_with("stream", "grp", msg_id)
    fake.xadd.assert_awaited_once()
    ok_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reclaim_stale_no_callback_xack_after_xadd() -> None:
    """When on_dead_letter is None, XACK happens immediately after XADD (backward compat)."""
    msg_id = "1680000000005c-0"
    claimed_entry = _claimed_entry(msg_id)
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=5)],
        claimed=[claimed_entry],
    )
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
        # no on_dead_letter
    )

    assert stats.dead_lettered == 1
    fake.xadd.assert_awaited_once()
    fake.xack.assert_awaited_once_with("stream", "grp", msg_id)


# ===========================================================================
# trim_deadletter — unit tests
# ===========================================================================


@pytest.mark.asyncio
async def test_trim_deadletter_calls_xtrim_with_correct_args() -> None:
    """trim_deadletter calls xtrim with minid derived from max_age_ms and approximate=True."""
    import time as _time

    redis = AsyncMock()
    redis.xtrim.return_value = 3

    max_age_ms = 30 * 86_400_000  # 30 days in milliseconds
    before_ms = int(_time.time() * 1000)
    result = await events.trim_deadletter(
        redis, "orpycamcp.signature.deadletter", max_age_ms=max_age_ms
    )
    after_ms = int(_time.time() * 1000)

    assert result == 3
    redis.xtrim.assert_awaited_once()
    call = redis.xtrim.await_args

    # Positional arg 0: stream name.
    assert call.args[0] == "orpycamcp.signature.deadletter"
    # approximate=True maps to the Redis ~ modifier (efficient MINID trim).
    assert call.kwargs.get("approximate") is True
    # minid is a string "<epoch_ms>-0" representing the cutoff Redis entry ID.
    min_id: str = call.kwargs.get("minid", "")
    assert min_id.endswith("-0"), f"minid should end with '-0', got: {min_id!r}"
    cutoff_ms = int(min_id[: -len("-0")])
    # The cutoff must be within the expected window (allow ±1 s for clock skew).
    assert before_ms - max_age_ms - 1000 <= cutoff_ms <= after_ms - max_age_ms + 1000


@pytest.mark.asyncio
async def test_trim_deadletter_returns_zero_when_xtrim_returns_none() -> None:
    """When xtrim returns None (driver variation), trim_deadletter returns 0 gracefully."""
    redis = AsyncMock()
    redis.xtrim.return_value = None

    result = await events.trim_deadletter(redis, "test.deadletter", max_age_ms=1_000)

    assert result == 0


@pytest.mark.asyncio
async def test_trim_deadletter_returns_zero_when_nothing_removed() -> None:
    """xtrim returns 0 (no entries older than horizon) → trim_deadletter returns 0."""
    redis = AsyncMock()
    redis.xtrim.return_value = 0

    result = await events.trim_deadletter(
        redis, "orpycamcp.workflow.deadletter", max_age_ms=7 * 86_400_000
    )

    assert result == 0


@pytest.mark.asyncio
async def test_reclaim_stale_returns_zero_stats_when_no_pending() -> None:
    """No pending entries → returns all-zero stats without calling any Redis command."""
    fake = _make_redis(pending=[], claimed=[])
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
    )

    assert stats == events.ReclaimStats()
    fake.xclaim.assert_not_awaited()
    fake.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_stale_failed_reprocess_at_threshold_dead_letters_with_last_error() -> None:
    """R-5b: handler fails and times_delivered+1 >= max_deliveries → DL in same sweep with last_error.

    XCLAIM increments the delivery counter internally.  When a fresh attempt
    fails and the effective count (times_delivered + 1) reaches the threshold,
    the message is dead-lettered immediately — capturing the concrete last_error
    rather than waiting for the next XPENDING scan (which would land on the
    direct-DL branch with dead_letter_last_error="").
    """
    msg_id = "1680000000006-0"
    claimed_entry = _claimed_entry(msg_id, tenant="demo")
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=4)],  # one below max
        claimed=[claimed_entry],
    )
    handler = AsyncMock(side_effect=ValueError("malformed payload — cannot decode nivel_nuevo"))
    callback = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
        on_dead_letter=callback,
    )

    # Reclaimed (XCLAIM attempted), reprocess failed, then dead-lettered.
    assert stats.reclaimed == 1
    assert stats.reprocessed_fail == 1
    assert stats.dead_lettered == 1
    assert stats.reprocessed_ok == 0

    # The message was routed to the dead-letter stream with the concrete error.
    fake.xadd.assert_awaited_once()
    dl_stream, dl_payload = fake.xadd.await_args.args
    assert dl_stream == "dl"
    assert dl_payload["dead_letter_reason"] == "max_deliveries_exceeded"
    assert dl_payload["dead_letter_delivery_count"] == "5"  # times_delivered + 1
    assert dl_payload["dead_letter_last_error"] == "malformed payload — cannot decode nivel_nuevo"

    # The message was acked after being published to the DL stream.
    fake.xack.assert_awaited_once_with("stream", "grp", msg_id)

    # The on_dead_letter callback was invoked with the enriched failure_meta.
    callback.assert_awaited_once()
    cb_envelope, cb_failure = callback.await_args.args
    assert cb_failure["dead_letter_last_error"] == "malformed payload — cannot decode nivel_nuevo"
    assert cb_failure["dead_letter_delivery_count"] == "5"
    assert cb_envelope.get("tenant_slug") == "demo"


@pytest.mark.asyncio
async def test_reclaim_stale_failed_reprocess_below_threshold_stays_in_pel() -> None:
    """R-5b: handler fails but times_delivered+1 < max_deliveries → NO DL, stays in PEL.

    Confirms the boundary: only when effective_count >= max_deliveries do we
    dead-letter in the same sweep.
    """
    msg_id = "1680000000007-0"
    claimed_entry = _claimed_entry(msg_id, tenant="demo")
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=3)],  # effective_count=4 < 5
        claimed=[claimed_entry],
    )
    handler = AsyncMock(side_effect=RuntimeError("transient error"))

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
    )

    assert stats.reprocessed_fail == 1
    assert stats.dead_lettered == 0
    assert stats.reprocessed_ok == 0
    # Message stays in PEL — neither acked nor routed to DL.
    fake.xack.assert_not_awaited()
    fake.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_stale_direct_dl_has_empty_last_error() -> None:
    """Direct-DL branch (orphan, no fresh attempt) → dead_letter_last_error is "" in failure_meta."""
    msg_id = "1680000000008-0"
    claimed_entry = _claimed_entry(msg_id)
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=5)],
        claimed=[claimed_entry],
    )
    callback = AsyncMock()
    handler = AsyncMock()

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
        on_dead_letter=callback,
    )

    assert stats.dead_lettered == 1
    dl_payload = fake.xadd.await_args.args[1]
    # Direct-DL path: no fresh attempt → last_error is explicitly empty string.
    assert dl_payload["dead_letter_last_error"] == ""
    # Callback also receives the failure_meta with the empty last_error.
    cb_failure = callback.await_args.args[1]
    assert cb_failure["dead_letter_last_error"] == ""


@pytest.mark.asyncio
async def test_reclaim_stale_r5b_callback_failure_blocks_ack() -> None:
    """R-4 / R-5b: handler fails at threshold AND callback fails → NO xack; dead_lettered=0.

    The XADD to the DL stream still happens (best-effort buffer) but the XACK is
    gated on the durable callback.  reprocessed_fail is still incremented because
    the handler did fail.
    """
    msg_id = "1680000000009-0"
    claimed_entry = _claimed_entry(msg_id, tenant="demo")
    fake = _make_redis(
        pending=[_pending_entry(msg_id, times_delivered=4)],  # effective_count=5 = max
        claimed=[claimed_entry],
    )
    handler = AsyncMock(side_effect=ValueError("malformed payload"))
    failing_callback = AsyncMock(side_effect=RuntimeError("postgres down"))

    stats = await events.reclaim_stale(
        fake, "stream", "grp", "consumer",
        handler=handler,
        min_idle_ms=60_000,
        max_deliveries=5,
        deadletter_stream="dl",
        on_dead_letter=failing_callback,
    )

    # Handler failed and callback failed → not committed.
    assert stats.reprocessed_fail == 1
    assert stats.dead_lettered == 0
    assert stats.reprocessed_ok == 0
    # XADD happened (best-effort buffer, before callback).
    fake.xadd.assert_awaited_once()
    # XACK must NOT have been called — message stays in PEL.
    fake.xack.assert_not_awaited()
