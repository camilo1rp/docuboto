# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de dead-letter y barrido de reclamación en workflow-service (R-4, ADR-021 Fase 2).

La cadencia de la reclamación es del bus (`docuboto.core.bus`, ADR-049) y se
prueba en `tests/document/integration/test_bus_eventos_integration.py`.

Cubre:
  4. _persist_dead_letter() — INSERT real + audit.append en la misma transacción.
  5. _persist_dead_letter() — idempotencia ON CONFLICT (no re-audita).
  6. _persist_dead_letter() — error transiente de BD propaga (no ack sin traza).
  7. _persist_dead_letter() — R1: envelope sin tenant_slug → audita bajo SYSTEM_TENANT
     (tenant_slug=None) vía public pool y retorna normalmente.

Patrón de mock:
  - acquire_tenant se parchea como asynccontextmanager que devuelve un AsyncMock.
  - _audit se parchea a nivel de módulo (app.workers.document_consumer._audit).
  - get_pool se parchea para el caso R1 (conexión a public schema sin tenant).
  - conn.fetchrow controla el resultado del INSERT (INSERT real vs ON CONFLICT).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT = "demo"
TRACKING = "2026-DEMO-E-000001"
DOC_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _make_conn(*, fetchrow_return: object = None) -> AsyncMock:
    """Devuelve un AsyncMock de conexión asyncpg con transaction() mockeado.

    ``fetchrow_return`` controla lo que devuelve ``conn.fetchrow``:
      - un dict-like → INSERT real (primera vez)
      - ``None`` → ON CONFLICT, no-op (segunda vez, idempotente)
      - side_effect con excepción → error de BD transitorio
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchrow.return_value = fetchrow_return
    return conn


def _fake_acquire_tenant(conn: AsyncMock):
    """Factory: devuelve un asynccontextmanager que produce la conn dada."""

    @asynccontextmanager
    async def _ctx(tenant_slug: str):  # noqa: ARG001
        yield conn

    return _ctx


def _dead_letter_envelope(*, tenant_slug: str = TENANT) -> dict:
    """Construye un sobre canónico para un evento document.radicado.created."""
    return {
        "event_id": "evt-dead-0001",
        "event_type": "document.radicado.created",
        "correlation_id": "corr-0001",
        "tenant_slug": tenant_slug,
        "occurred_at": "2026-06-29T10:00:00+00:00",
        "actor": "user-original-99",
        "payload": {
            "tracking_number": TRACKING,
            "id": DOC_UUID,
            "doc_type": "E",
        },
    }


def _failure_meta() -> dict:
    return {
        "dead_letter_reason": "max_deliveries_exceeded",
        "dead_letter_origin_stream": "orpycamcp.document.events",
        "dead_letter_group": "workflow-service",
        "dead_letter_consumer": "workflow-service-1",
        "dead_letter_delivery_count": "5",
        "dead_lettered_at": "2026-06-29T10:01:00+00:00",
        "dead_letter_last_error": "connection reset by peer",
    }


# ===========================================================================
# 4. _persist_dead_letter — INSERT real + audit en la misma transacción
# ===========================================================================


@pytest.mark.asyncio
async def test_persist_dead_letter_inserta_y_audita() -> None:
    """R-4/ADR-021: primera llamada → INSERT real → audit.append invocado en la misma tx.

    Verifica:
    - DeadLetterRepository.insert_if_new es llamado (vía conn.fetchrow).
    - Si fetchrow devuelve una fila → audit.append se llama con actor='system'.
    - El payload de auditoría tiene las claves 'failure', 'envelope' y 'business'.
    - R-9: actor en audit es 'system'; actor original está en payload['envelope']['actor'].
    """
    from workflow.workers.document_consumer import _persist_dead_letter

    row_mock = {"event_id": "evt-dead-0001"}
    conn = _make_conn(fetchrow_return=row_mock)
    mock_audit = AsyncMock()

    envelope = _dead_letter_envelope()
    failure = _failure_meta()

    with (
        patch("workflow.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("workflow.workers.document_consumer._audit", mock_audit),
        patch("workflow.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _persist_dead_letter(envelope, failure)

    # INSERT fue ejecutado (via fetchrow en DeadLetterRepository).
    conn.fetchrow.assert_called_once()

    # audit.append fue invocado dentro de la misma transacción.
    mock_audit.append.assert_called_once()
    kw = mock_audit.append.call_args.kwargs

    # R-9: el actor del acto es "system" (el worker), no el usuario original.
    assert kw["actor"] == "system"
    assert kw["canal"] == "system"
    assert kw["action"] == "evento.dead_letter"
    assert kw["object_ref"] == "evt-dead-0001"
    assert kw["tenant_slug"] == TENANT

    payload = kw["payload"]

    # "failure" contiene el failure_meta completo.
    assert payload["failure"]["dead_letter_last_error"] == "connection reset by peer"
    assert payload["failure"]["dead_letter_delivery_count"] == "5"

    # "envelope" preserva el sobre íntegro con el actor original.
    assert payload["envelope"]["actor"] == "user-original-99"
    assert payload["envelope"]["event_type"] == "document.radicado.created"
    assert payload["envelope"]["correlation_id"] == "corr-0001"
    assert payload["envelope"]["payload"]["tracking_number"] == TRACKING

    # "business" expone identificadores de negocio para consultas rápidas.
    assert payload["business"]["tracking_number"] == TRACKING
    assert payload["business"]["doc_id"] == DOC_UUID


# ===========================================================================
# 5. _persist_dead_letter — idempotencia ON CONFLICT
# ===========================================================================


@pytest.mark.asyncio
async def test_persist_dead_letter_idempotente_no_audita_en_conflict() -> None:
    """R-4 idempotencia: ON CONFLICT (event_id) → fetchrow devuelve None → audit NO se llama.

    La segunda entrega del mismo evento (at-least-once, XACK falló) produce un
    ON CONFLICT DO NOTHING.  El INSERT no devuelve fila → audit_log NO se escribe
    de nuevo → no hay doble traza.
    """
    from workflow.workers.document_consumer import _persist_dead_letter

    # fetchrow devuelve None → ON CONFLICT, no-op.
    conn = _make_conn(fetchrow_return=None)
    mock_audit = AsyncMock()

    with (
        patch("workflow.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("workflow.workers.document_consumer._audit", mock_audit),
        patch("workflow.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _persist_dead_letter(_dead_letter_envelope(), _failure_meta())

    # fetchrow fue llamado (el INSERT se intentó).
    conn.fetchrow.assert_called_once()
    # Pero NO se auditó (ON CONFLICT → sin fila devuelta).
    mock_audit.append.assert_not_called()


# ===========================================================================
# 6. _persist_dead_letter — error transiente propaga
# ===========================================================================


@pytest.mark.asyncio
async def test_persist_dead_letter_error_transiente_propaga() -> None:
    """R-4: error transitorio de BD en fetchrow → excepción propaga (no se traga).

    El helper reclaim_stale interpretará la excepción como callback fallido y
    NO ackeará → el mensaje permanece en PEL para reintento.
    """
    from workflow.workers.document_consumer import _persist_dead_letter

    conn = _make_conn()
    conn.fetchrow.side_effect = OSError("connection reset by peer")

    envelope = _dead_letter_envelope()
    failure = _failure_meta()

    with patch("workflow.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)):
        with pytest.raises(OSError, match="connection reset by peer"):
            await _persist_dead_letter(envelope, failure)


# ===========================================================================
# 7. _persist_dead_letter — R1: sin tenant_slug → SYSTEM_TENANT
# ===========================================================================


@pytest.mark.asyncio
async def test_persist_dead_letter_sin_tenant_slug_audita_system_y_retorna() -> None:
    """R1 (workflow): envelope sin tenant_slug → audita bajo SYSTEM_TENANT y retorna.

    Diferencia clave vs signature-service: en vez de solo loguear, workflow-service
    escribe en public.audit_log bajo __system__ para dejar traza inmutable incluso
    del poison pill malformado.

    - acquire_tenant NO se llama (no hay schema destino).
    - audit.append SÍ se llama con tenant_slug=None (→ SYSTEM_TENANT).
    - La función retorna normalmente (no lanza) → el helper puede XACK el poison pill.
    - Si el audit falla por error transiente, la excepción propaga (R-4: sin ack sin traza).
    """
    from workflow.workers.document_consumer import _persist_dead_letter

    mock_audit = AsyncMock()

    # Mock pool.acquire() como context manager que entrega una conn.
    mock_pub_conn = AsyncMock()
    mock_pub_conn.transaction = MagicMock(return_value=AsyncMock())
    mock_acquire_cm = MagicMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_pub_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire_cm

    # Sobre sin tenant_slug — evento permanentemente malformado.
    envelope = {
        "event_id": "poison-pill-no-tenant",
        "event_type": "document.radicado.created",
        "actor": "user-x",
        "payload": {"tracking_number": TRACKING},
        # SIN tenant_slug
    }
    failure = _failure_meta()

    with (
        patch("workflow.workers.document_consumer._audit", mock_audit),
        patch("workflow.workers.document_consumer._AUDIT_AVAILABLE", True),
        patch("workflow.workers.document_consumer.get_pool", return_value=mock_pool),
        patch("workflow.workers.document_consumer.acquire_tenant") as mock_acq,
    ):
        # No debe lanzar — retorna normalmente para que el helper pueda XACK.
        await _persist_dead_letter(envelope, failure)

    # acquire_tenant no fue invocado — no hay schema tenant destino.
    mock_acq.assert_not_called()

    # R1: audit.append fue llamado con tenant_slug=None (escribe bajo SYSTEM_TENANT).
    mock_audit.append.assert_called_once()
    kw = mock_audit.append.call_args.kwargs
    assert kw["tenant_slug"] is None
    assert kw["actor"] == "system"
    assert kw["canal"] == "system"
    assert kw["action"] == "evento.dead_letter"
    assert kw["object_ref"] == "poison-pill-no-tenant"

    audit_payload = kw["payload"]
    assert "note" in audit_payload
    assert "SYSTEM_TENANT" in audit_payload["note"]
    # El sobre original está preservado en el payload.
    assert audit_payload["envelope"]["event_id"] == "poison-pill-no-tenant"
    assert "failure" in audit_payload


@pytest.mark.asyncio
async def test_persist_dead_letter_sin_tenant_slug_error_transiente_propaga() -> None:
    """R1: si el audit SYSTEM_TENANT falla por error transiente → propaga (no ack).

    Garantiza que incluso en el path R1 se cumple R-4: si no se puede escribir la
    traza, el helper no puede XACK (el mensaje permanece en PEL para reintento).
    """
    from workflow.workers.document_consumer import _persist_dead_letter

    mock_audit = AsyncMock()
    mock_audit.append.side_effect = OSError("postgres unavailable")

    mock_pub_conn = AsyncMock()
    mock_acquire_cm = MagicMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_pub_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire_cm

    envelope = {"event_id": "poison-pill-2", "payload": {}}  # sin tenant_slug
    failure = {"dead_letter_reason": "max_deliveries_exceeded"}

    with (
        patch("workflow.workers.document_consumer._audit", mock_audit),
        patch("workflow.workers.document_consumer._AUDIT_AVAILABLE", True),
        patch("workflow.workers.document_consumer.get_pool", return_value=mock_pool),
    ):
        with pytest.raises(OSError, match="postgres unavailable"):
            await _persist_dead_letter(envelope, failure)
