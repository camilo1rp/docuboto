# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de idempotencia (insert-then-send) en event_consumer (ADR-021 Fase 3).

Cubre:
  1. claim True (evento nuevo) → dispatch_webhooks y send_notification son llamados.
  2. claim False (ya procesado) → skip silencioso; ningún efecto externo.
  3. BD caída en claim → excepción propaga al caller (A-1: no ack → PEL).
  4. envelope sin tenant_slug → ValueError propaga (poison pill → dead-letter R1).
  5. envelope sin event_id → ValueError propaga.
  6. process_document_event: dedup + dispatch + notificación para radicado.created.
  7. process_document_event: dedup + dispatch sin notificación para tipo desconocido.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT = "demo"


def _raw_fields(
    *,
    event_id: str = "evt-0001",
    event_type: str = "workflow.step.created",
    tenant_slug: str = TENANT,
    payload: dict | None = None,
) -> dict[str, str]:
    """Campos de un sobre canónico, tal como los deja un productor legítimo.

    Pasa por `to_fields` en vez de construir el diccionario a mano. La diferencia
    importa desde que el sobre va firmado: un diccionario armado aquí es
    exactamente lo que fabricaría quien escribiera en Redis por su cuenta, así
    que estos tests estarían afirmando que el consumidor acepta sobres sin
    origen — justo lo que se corrigió.
    """
    from orpycamcp_common.events import to_fields

    return to_fields(
        {
            "event_id": event_id,
            "event_type": event_type,
            "version": 1,
            "tenant_slug": tenant_slug,
            "occurred_at": "2026-06-30T10:00:00+00:00",
            "actor": "user-001",
            "correlation_id": "corr-001",
            "dedup_key": "",
            "payload": payload or {},
        }
    )


#: Nombre REAL de una dependencia, con espacio y tilde. Las fixtures usaban
#: identificadores ASCII (`"001"`, `"juridica"`) y por eso estos tests estuvieron
#: en verde mientras producción fallaba el 100% de los envíos: elegían el único
#: valor que no podía romperse. `to_dept` es el NOMBRE por convención del sistema.
DEPENDENCIA = "Oficina Jurídica"
CODIGO_DEPENDENCIA = 902


def _make_conn(*, claim_result: bool = True) -> AsyncMock:
    """Mock asyncpg connection: reparte `fetchrow` según la consulta.

    La misma conexión sirve a dos consultas distintas —el `claim` del evento y
    la resolución del buzón de la dependencia—, así que un `return_value` único
    daría una de las dos por la otra.
    """
    conn = AsyncMock()

    async def _fetchrow(sql: str, *args):  # noqa: ARG001
        if "dependencias" in sql:
            return {"codigo": CODIGO_DEPENDENCIA}
        # ProcessedEventsRepository.claim: registro no-None → claimed (True).
        return {"event_id": "evt-0001"} if claim_result else None

    conn.fetchrow.side_effect = _fetchrow
    return conn


def _fake_acquire(conn: AsyncMock):
    """Return an asynccontextmanager that yields the given connection."""

    @asynccontextmanager
    async def _ctx(tenant_slug: str):  # noqa: ARG001
        yield conn

    return _ctx


# ---------------------------------------------------------------------------
# Tests — process_event (workflow stream)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_event_claim_true_despacha_efectos() -> None:
    """claim True (evento nuevo) → dispatch_webhooks y send_notification son invocados."""
    from app.services import event_consumer

    conn = _make_conn(claim_result=True)
    raw = _raw_fields(
        event_type="workflow.step.created",
        payload={"action": "assign", "tracking_number": "2026-DEMO-E-000001", "to_dept": DEPENDENCIA},
    )

    with (
        patch.object(event_consumer, "acquire_tenant", _fake_acquire(conn)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as mock_dispatch,
        patch.object(event_consumer, "send_notification", AsyncMock()) as mock_send,
    ):
        await event_consumer.process_event(raw)

    mock_dispatch.assert_called_once()
    mock_send.assert_called_once()
    sent_payload = mock_send.call_args.args[0]
    assert "2026-DEMO-E-000001" in sent_payload.subject


@pytest.mark.asyncio
async def test_process_event_claim_false_skip_sin_efectos() -> None:
    """claim False (ya procesado) → ningún efecto externo; retorno silencioso."""
    from app.services import event_consumer

    conn = _make_conn(claim_result=False)
    raw = _raw_fields(
        event_type="workflow.step.created",
        payload={"action": "assign", "tracking_number": "2026-DEMO-E-000001", "to_dept": DEPENDENCIA},
    )

    with (
        patch.object(event_consumer, "acquire_tenant", _fake_acquire(conn)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as mock_dispatch,
        patch.object(event_consumer, "send_notification", AsyncMock()) as mock_send,
    ):
        await event_consumer.process_event(raw)

    mock_dispatch.assert_not_called()
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_process_event_bd_caida_en_claim_propaga() -> None:
    """Error de BD en claim → excepción propaga (A-1: caller no ackeará → PEL)."""
    from app.services import event_consumer

    conn = AsyncMock()
    conn.fetchrow.side_effect = OSError("connection lost")
    raw = _raw_fields(event_type="workflow.step.created", payload={})

    with (
        patch.object(event_consumer, "acquire_tenant", _fake_acquire(conn)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as mock_dispatch,
        patch.object(event_consumer, "send_notification", AsyncMock()) as mock_send,
    ):
        with pytest.raises(OSError, match="connection lost"):
            await event_consumer.process_event(raw)

    # No external effects triggered — the error happened before the claim succeeded.
    mock_dispatch.assert_not_called()
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_process_event_sin_tenant_slug_raises() -> None:
    """Envelope sin tenant_slug → ValueError propaga (dead-letter R1 en el reclaim)."""
    from app.services import event_consumer

    # El sobre nace SIN entidad y se firma así: lo que se prueba es la guarda
    # del consumidor ante un sobre incompleto pero legítimo, no la firma. Vaciar
    # el campo DESPUÉS de firmar probaría otra cosa —un sobre manipulado— y esa
    # ya tiene su propio test.
    raw = _raw_fields(event_type="workflow.step.created", tenant_slug="")

    with pytest.raises(ValueError, match="tenant_slug"):
        await event_consumer.process_event(raw)


@pytest.mark.asyncio
async def test_process_event_sin_event_id_raises() -> None:
    """Envelope sin event_id → ValueError propaga (no se puede deduplicar)."""
    from app.services import event_consumer

    # Igual que el de arriba: nace sin identificador y se firma así.
    raw = _raw_fields(event_type="workflow.step.created", event_id="")

    with pytest.raises(ValueError, match="event_id"):
        await event_consumer.process_event(raw)


@pytest.mark.asyncio
async def test_process_event_overdue_claim_true() -> None:
    """workflow.step.overdue con claim True → notificación de vencimiento enviada."""
    from app.services import event_consumer

    conn = _make_conn(claim_result=True)
    raw = _raw_fields(
        event_type="workflow.step.overdue",
        payload={"tracking_number": "2026-DEMO-E-000002", "to_dept": DEPENDENCIA, "due_at": "2026-06-01"},
    )

    with (
        patch.object(event_consumer, "acquire_tenant", _fake_acquire(conn)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()),
        patch.object(event_consumer, "send_notification", AsyncMock()) as mock_send,
    ):
        await event_consumer.process_event(raw)

    mock_send.assert_called_once()
    sent_payload = mock_send.call_args.args[0]
    assert "VENCIDO" in sent_payload.subject


# ---------------------------------------------------------------------------
# Tests — process_document_event (document stream)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_document_event_radicado_created_claim_true() -> None:
    """document.radicado.created con claim True → webhook + notificación."""
    from app.services import event_consumer

    conn = _make_conn(claim_result=True)
    raw = _raw_fields(
        event_type="document.radicado.created",
        payload={"tracking_number": "2026-DEMO-E-000003", "dest_dept_code": "007"},
    )

    with (
        patch.object(event_consumer, "acquire_tenant", _fake_acquire(conn)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as mock_dispatch,
        patch.object(event_consumer, "send_notification", AsyncMock()) as mock_send,
    ):
        await event_consumer.process_document_event(raw)

    mock_dispatch.assert_called_once()
    mock_send.assert_called_once()
    sent = mock_send.call_args.args[0]
    assert "2026-DEMO-E-000003" in sent.subject
    assert "dependencia-007" in sent.recipient_email


@pytest.mark.asyncio
async def test_process_document_event_claim_false_skip() -> None:
    """document event con claim False → dedup skip; sin webhook ni email."""
    from app.services import event_consumer

    conn = _make_conn(claim_result=False)
    raw = _raw_fields(event_type="document.radicado.created", payload={})

    with (
        patch.object(event_consumer, "acquire_tenant", _fake_acquire(conn)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as mock_dispatch,
        patch.object(event_consumer, "send_notification", AsyncMock()) as mock_send,
    ):
        await event_consumer.process_document_event(raw)

    mock_dispatch.assert_not_called()
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_event_tipo_desconocido_solo_webhook() -> None:
    """Tipo de evento desconocido con claim True → webhook dispatched, sin email."""
    from app.services import event_consumer

    conn = _make_conn(claim_result=True)
    raw = _raw_fields(event_type="document.radicado.updated", payload={})

    with (
        patch.object(event_consumer, "acquire_tenant", _fake_acquire(conn)),
        patch.object(event_consumer, "dispatch_webhooks", AsyncMock()) as mock_dispatch,
        patch.object(event_consumer, "send_notification", AsyncMock()) as mock_send,
    ):
        await event_consumer.process_document_event(raw)

    # Webhook dispatched (exactly-once for all event types).
    mock_dispatch.assert_called_once()
    # No email for unknown event types.
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_event_sin_tenant_slug_raises() -> None:
    """document event sin tenant_slug → ValueError propaga."""
    from app.services import event_consumer

    raw = _raw_fields(event_type="document.radicado.created", tenant_slug="")

    with pytest.raises(ValueError, match="tenant_slug"):
        await event_consumer.process_document_event(raw)
