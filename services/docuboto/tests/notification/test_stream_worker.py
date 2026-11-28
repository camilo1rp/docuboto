# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del worker de notification-service sobre el bus en PostgreSQL (E16, ADR-049).

El bucle (tomar, confirmar, reclamar, aparcar) es el de `docuboto.core.bus` y se
prueba contra Postgres real en `tests/document/integration/test_bus_eventos_integration.py`.
Aquí, lo propio del worker:
  1. `run()` se engancha al bus con su grupo, sus dos streams, su handler, su
     dead-letter y su mantenimiento diario.
  2. `_handle` enruta por tipo de evento: los de flujo a `process_event`, el resto
     a `process_document_event`.
  3. `_mantenimiento_diario` corre la retención de dedup (la de dead-letters es del barrido).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from notification.workers import stream_worker


@pytest.mark.asyncio
async def test_run_se_engancha_al_bus_con_lo_suyo() -> None:
    with patch.object(stream_worker.bus, "consumir", new=AsyncMock()) as consumir:
        await stream_worker.run()

    consumir.assert_awaited_once()
    kw = consumir.await_args.kwargs
    assert kw["group"] == stream_worker.GROUP
    assert set(kw["streams"]) == {stream_worker.WORKFLOW_STREAM, stream_worker.DOCUMENT_STREAM}
    assert kw["handler"] is stream_worker._handle
    assert kw["on_dead_letter"] is stream_worker._persist_dead_letter
    assert kw["periodico"] is stream_worker._mantenimiento_diario
    assert kw["intervalo_periodico"] == stream_worker.RETENTION_INTERVAL
    assert kw["status_name"] == "notification"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tipo", "esperado"),
    [
        ("workflow.step.created", "process_event"),
        ("workflow.step.overdue", "process_event"),
        ("document.radicado.created", "process_document_event"),
        ("document.envio.devuelto", "process_document_event"),
        ("documento.contenido_cambiado", "process_document_event"),
        ("", "process_document_event"),
    ],
)
async def test_handle_enruta_por_tipo(tipo: str, esperado: str) -> None:
    campos = {"event_id": "e-1", "event_type": tipo}
    with (
        patch.object(stream_worker, "process_event", new=AsyncMock()) as flujo,
        patch.object(stream_worker, "process_document_event", new=AsyncMock()) as doc,
    ):
        await stream_worker._handle(campos)

    llamado, no_llamado = (flujo, doc) if esperado == "process_event" else (doc, flujo)
    llamado.assert_awaited_once_with(campos)
    no_llamado.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_propaga_el_fallo_para_que_quede_pendiente() -> None:
    """Confirmar solo si el handler terminó: el error tiene que llegar al bus."""
    with patch.object(
        stream_worker, "process_document_event", new=AsyncMock(side_effect=RuntimeError("smtp"))
    ):
        with pytest.raises(RuntimeError, match="smtp"):
            await stream_worker._handle({"event_type": "document.radicado.created"})


@pytest.mark.asyncio
async def test_mantenimiento_diario_corre_la_retencion_de_dedup() -> None:
    with patch.object(stream_worker.retention, "run_once", new=AsyncMock()) as dedup:
        await stream_worker._mantenimiento_diario()
    dedup.assert_awaited_once()


@pytest.mark.asyncio
async def test_un_fallo_de_la_retencion_lo_aisla_el_bus() -> None:
    """Sube al bus, que lo registra y sigue consumiendo (`tests/docuboto/test_bus.py`)."""
    with patch.object(
        stream_worker.retention, "run_once", new=AsyncMock(side_effect=RuntimeError("db"))
    ):
        with pytest.raises(RuntimeError, match="db"):
            await stream_worker._mantenimiento_diario()
