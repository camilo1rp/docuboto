# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El núcleo que reprocesa cada grupo desde la consola del dead-letter (ADR-049 §4).

La consola pone lo común (bloqueo, estado, asiento); aquí, lo que cada grupo hace
en la conexión de la consola y lo que devuelve como `outcome`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from docuboto.core.dead_letter import ReplayFallido
from notification.services import replay as notification_replay
from notification.workers.stream_worker import DOCUMENT_STREAM, WORKFLOW_STREAM
from signature.workers import document_consumer as signature_consumer
from workflow.workers import document_consumer as workflow_consumer

pytestmark = pytest.mark.asyncio

SOBRE = {"event_id": "e-1", "tenant_slug": "demo", "payload": {"id": "r-1", "due_date": None}}


def _kw(**extra) -> dict:
    return {"event_id": "e-1", "envelope": SOBRE, "actor": "u-1", **extra}


# ── workflow ──────────────────────────────────────────────────────────────────


async def test_workflow_reparte_otra_vez_en_la_conexion_de_la_consola(monkeypatch) -> None:
    repartir = AsyncMock(return_value="Oficina Jurídica")
    monkeypatch.setattr(workflow_consumer, "apply_distribution", repartir)
    conn = object()
    salida = await workflow_consumer.replay(conn, "demo", event_type="document.radicado.created", **_kw())
    assert salida == {"assigned_to": "Oficina Jurídica"}
    repartir.assert_awaited_once_with(conn, "demo", SOBRE["payload"])


async def test_workflow_realinea_el_vencimiento_si_el_evento_es_ese(monkeypatch) -> None:
    alinear = AsyncMock()
    repartir = AsyncMock()
    monkeypatch.setattr(workflow_consumer, "alinear_vencimiento", alinear)
    monkeypatch.setattr(workflow_consumer, "apply_distribution", repartir)
    conn = object()
    salida = await workflow_consumer.replay(
        conn, "demo", event_type=workflow_consumer.VENCIMIENTO_CAMBIADO, **_kw()
    )
    assert salida == {}
    alinear.assert_awaited_once_with(conn, SOBRE["payload"])
    repartir.assert_not_awaited()


# ── signature ─────────────────────────────────────────────────────────────────


async def test_signature_reaplica_el_evento_atribuido_a_quien_reprocesa(monkeypatch) -> None:
    aplicar = AsyncMock(return_value=1)
    monkeypatch.setattr(signature_consumer, "apply_event", aplicar)
    conn = object()
    salida = await signature_consumer.replay(
        conn, "demo", event_type="document.radicado.reclassified", **_kw()
    )
    assert salida == {}
    aplicar.assert_awaited_once_with(
        conn,
        "demo",
        "document.radicado.reclassified",
        SOBRE["payload"],
        event_id="e-1",
        actor="u-1",
        replayed=True,
    )


# ── notification ──────────────────────────────────────────────────────────────


@pytest.fixture
def correo(monkeypatch):
    construir = AsyncMock(return_value=["a", "b"])
    borrar = AsyncMock()
    monkeypatch.setattr(notification_replay, "_construir_envios", construir)
    monkeypatch.setattr(
        notification_replay, "ProcessedEventsRepository",
        lambda _conn: SimpleNamespace(delete_one=borrar),
    )
    return construir, borrar


async def test_notification_rechaza_un_stream_desconocido(correo) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await notification_replay.replay(object(), "demo", event_type="x", origin_stream="otro", **_kw())
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "unknown_origin_stream"
    correo[0].assert_not_awaited()


@pytest.mark.parametrize("stream", [WORKFLOW_STREAM, DOCUMENT_STREAM])
async def test_notification_reenvia_y_cuenta_lo_enviado(monkeypatch, correo, stream) -> None:
    construir, borrar = correo
    enviar = AsyncMock(return_value=SimpleNamespace(status="sent"))
    monkeypatch.setattr(notification_replay, "send_notification", enviar)
    salida = await notification_replay.replay(
        object(), "demo", event_type="workflow.step.created", origin_stream=stream, **_kw()
    )
    assert salida == {"notifications_sent": 2}
    borrar.assert_awaited_once_with("e-1")
    assert construir.await_args.args[1] == stream
    assert enviar.await_count == 2


async def test_notification_un_envio_fallido_deja_el_dead_letter_pendiente(monkeypatch, correo) -> None:
    enviar = AsyncMock(side_effect=[SimpleNamespace(status="sent"), SimpleNamespace(status="failed")])
    monkeypatch.setattr(notification_replay, "send_notification", enviar)
    with pytest.raises(ReplayFallido, match="1 de 2 envíos fallaron"):
        await notification_replay.replay(
            object(), "demo", event_type="x", origin_stream=DOCUMENT_STREAM, **_kw()
        )
