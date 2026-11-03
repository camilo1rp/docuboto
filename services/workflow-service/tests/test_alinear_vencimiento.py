# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El consumidor alinea el plazo del paso activo cuando cambia el del radicado."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from workflow.workers import document_consumer as consumer


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


def _sobre(tipo, payload):
    return {
        "event_type": tipo,
        "tenant_slug": "demo",
        "payload": json.dumps(payload),
    }


@pytest.mark.asyncio
async def test_copia_la_fecha_al_final_del_dia_en_los_pasos_activos():
    conn = AsyncMock()
    await consumer.alinear_vencimiento(conn, {"id": "abc", "due_date": "2026-03-01"})
    sql, rid, due = conn.execute.await_args.args
    assert "pending" in sql and "in_progress" in sql
    assert rid == "abc"
    assert due == datetime(2026, 3, 1, 23, 59, tzinfo=UTC)


@pytest.mark.asyncio
async def test_el_evento_de_vencimiento_no_redistribuye(monkeypatch):
    dist = AsyncMock()
    conn = AsyncMock()
    monkeypatch.setattr(consumer, "apply_distribution", dist)
    monkeypatch.setattr(consumer, "acquire_tenant", lambda slug: _Ctx(conn))
    await consumer._handle(
        _sobre(
            "document.radicado.due_date_changed",
            {"id": "abc", "due_date": "2026-03-01"},
        )
    )
    dist.assert_not_awaited()
    conn.execute.assert_awaited()


@pytest.mark.asyncio
async def test_un_alta_sigue_distribuyendo(monkeypatch):
    dist = AsyncMock()
    monkeypatch.setattr(consumer, "apply_distribution", dist)
    monkeypatch.setattr(consumer, "acquire_tenant", lambda slug: _Ctx(AsyncMock()))
    await consumer._handle(_sobre("document.radicado.created", {"id": "abc"}))
    dist.assert_awaited()
