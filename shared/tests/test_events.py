# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de orpycamcp_common.events: el sobre y la publicación, sin base.

El bus contra la tabla de verdad (tomar, confirmar, reclamar, aparcar, purgar)
lo prueba `services/docuboto/tests/document/integration/test_bus_eventos_integration.py`.
"""

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


class _Conn:
    """Lo mínimo de asyncpg que tocan `publish` y `emit`."""

    def __init__(self) -> None:
        self.fetchvals: list[tuple] = []
        self.executes: list[tuple] = []

    async def fetchval(self, sql: str, *args):
        self.fetchvals.append((sql, *args))
        if "clock_timestamp" in sql:
            from datetime import UTC, datetime

            return datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
        return 41

    async def execute(self, sql: str, *args):
        self.executes.append((sql, *args))
        return "SELECT 1"


@pytest.mark.asyncio
async def test_publish_inserta_el_sobre_plano_y_firmado_y_avisa_con_el_slug(monkeypatch) -> None:
    import json

    monkeypatch.setenv("EVENT_SIGNING_SECRET", "s" * 32)
    conn = _Conn()
    ev = events.build_event("x.y", tenant_slug="demo", payload={"k": 1})

    seq = await events.publish(conn, "orpycamcp.document.events", ev)

    assert seq == 41
    sql, stream, event_id, sobre = conn.fetchvals[0]
    assert "INSERT INTO eventos" in sql
    assert (stream, event_id) == ("orpycamcp.document.events", ev["event_id"])
    campos = json.loads(sobre)
    assert campos["firma"], "el sobre se guarda firmado"
    assert events.parse_fields(campos)["payload"] == {"k": 1}
    # El NOTIFY lleva el slug, nunca el evento (límite de 8.000 bytes).
    assert conn.executes == [("SELECT pg_notify($1, $2)", events.CANAL, "demo")]


@pytest.mark.asyncio
async def test_emit_toma_el_instante_del_reloj_de_la_base() -> None:
    conn = _Conn()
    ev = await events.emit(conn, "s", "x.y", tenant_slug="demo", payload={"a": 1})
    assert ev["occurred_at"] == "2026-11-27T12:00:00+00:00"
    assert ev["event_type"] == "x.y" and ev["payload"] == {"a": 1}
    assert any("clock_timestamp" in f[0] for f in conn.fetchvals)


@pytest.mark.asyncio
async def test_emit_respeta_el_instante_que_le_dan() -> None:
    conn = _Conn()
    ev = await events.emit(conn, "s", "x.y", tenant_slug="demo", occurred_at="2020-01-01T00:00:00+00:00")
    assert ev["occurred_at"] == "2020-01-01T00:00:00+00:00"
    assert not any("clock_timestamp" in f[0] for f in conn.fetchvals)


def test_los_campos_del_sobre_llegan_como_texto_o_ya_decodificados() -> None:
    campos = events.to_fields(events.build_event("x.y", tenant_slug="demo"))
    import json

    assert events._campos(json.dumps(campos)) == campos
    assert events._campos(campos) == campos
