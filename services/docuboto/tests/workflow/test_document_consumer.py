# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del consumidor de eventos de documentos en workflow-service (E05, ADR-049).

El bucle (tomar, confirmar, reclamar, aparcar) es el del bus en PostgreSQL
(`docuboto.core.bus`) y se prueba contra la tabla de verdad en
`tests/document/integration/test_bus_eventos_integration.py`. Aquí se prueba lo
propio de workflow: qué le pasa al bus y qué hace `_handle` con cada evento.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from orpycamcp_common import events

from workflow.core.constants import DLQ_GROUP
from workflow.workers import document_consumer


@pytest.mark.asyncio
async def test_run_delega_en_el_bus_con_lo_propio_de_workflow() -> None:
    consumir = AsyncMock()
    with patch.object(document_consumer.bus, "consumir", consumir):
        await document_consumer.run()

    consumir.assert_awaited_once()
    kw = consumir.await_args.kwargs
    assert kw["group"] == DLQ_GROUP
    assert kw["streams"] == (document_consumer.DOCUMENT_STREAM,)
    assert kw["handler"] is document_consumer._handle
    assert kw["on_dead_letter"] is document_consumer._persist_dead_letter
    assert kw["status_name"] == "workflow"
    # La retención de sus dead-letters la hace el barrido para todos los grupos.
    assert "periodico" not in kw


def _campos(event_type: str, payload: dict, tenant_slug: str = "demo") -> dict:
    return events.to_fields(
        events.build_event(event_type, tenant_slug=tenant_slug, payload=payload)
    )


def _acquire(conn):
    @asynccontextmanager
    async def _cm(_slug):
        yield conn

    return _cm


@pytest.mark.asyncio
async def test_handle_radicado_creado_distribuye() -> None:
    conn = AsyncMock()
    distribuir = AsyncMock()
    with (
        patch.object(document_consumer, "acquire_tenant", _acquire(conn)),
        patch.object(document_consumer, "apply_distribution", distribuir),
    ):
        await document_consumer._handle(
            _campos("document.radicado.created", {"id": "r-1"})
        )
    distribuir.assert_awaited_once_with(conn, "demo", {"id": "r-1"})


@pytest.mark.asyncio
async def test_handle_vencimiento_cambiado_alinea_los_pasos() -> None:
    conn = AsyncMock()
    alinear = AsyncMock()
    with (
        patch.object(document_consumer, "acquire_tenant", _acquire(conn)),
        patch.object(document_consumer, "alinear_vencimiento", alinear),
    ):
        await document_consumer._handle(
            _campos(document_consumer.VENCIMIENTO_CAMBIADO, {"id": "r-1", "due_date": "2026-12-01"})
        )
    alinear.assert_awaited_once_with(conn, {"id": "r-1", "due_date": "2026-12-01"})


@pytest.mark.asyncio
async def test_handle_ignora_lo_que_no_es_suyo_o_viene_incompleto() -> None:
    distribuir = AsyncMock()
    alinear = AsyncMock()
    with (
        patch.object(document_consumer, "acquire_tenant", _acquire(AsyncMock())),
        patch.object(document_consumer, "apply_distribution", distribuir),
        patch.object(document_consumer, "alinear_vencimiento", alinear),
    ):
        await document_consumer._handle(_campos("document.anexo.added", {"id": "r-1"}))
        await document_consumer._handle(_campos("document.radicado.created", {}))
    distribuir.assert_not_awaited()
    alinear.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_propaga_el_fallo_para_que_el_bus_no_confirme() -> None:
    with (
        patch.object(document_consumer, "acquire_tenant", _acquire(AsyncMock())),
        patch.object(
            document_consumer, "apply_distribution", AsyncMock(side_effect=RuntimeError("bd"))
        ),
        pytest.raises(RuntimeError),
    ):
        await document_consumer._handle(_campos("document.radicado.created", {"id": "r-1"}))


def test_el_sobre_sin_firma_valida_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENT_SIGNING_SECRET", "s" * 32)
    campos = _campos("document.radicado.created", {"id": "r-1"})
    campos["payload"] = json.dumps({"id": "otro"})
    with pytest.raises(events.EventoNoAutentico):
        events.parse_fields(campos)
