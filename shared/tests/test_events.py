# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de orpycamcp_common.events (sobre canónico; sin Redis real)."""

from unittest.mock import AsyncMock

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
