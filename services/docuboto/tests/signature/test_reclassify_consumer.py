# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de reclasificación y dead-letter en signature-service (E08, RF-SEG-08, R-4/ADR-021).

Cubre:
  1. CadenaRepository.actualizar_nivel_seguridad — repositorio puro (conn mockeado).
  2. _handle() del document_consumer — consumidor del bus de eventos con
     acquire_tenant y _audit patcheados.
  3. run() — xack condicional al éxito de _handle (A-1).
  4. _persist_dead_letter() — callback de dead-letter durable (R-4 / ADR-021):
     insert+audit en transacción; idempotencia ON CONFLICT; error transiente propaga.

Patrón de mock:
  - acquire_tenant se parchea como asynccontextmanager que devuelve un AsyncMock.
  - _audit se parchea a nivel de módulo (app.workers.document_consumer._audit)
    con un AsyncMock para .append.
  - DeadLetterRepository se parchea o la conn.fetchrow controla el resultado del INSERT.
  - Los campos planos para _handle() se construyen con events.to_fields() +
    events.build_event() del sobre canónico.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from orpycamcp_common import events

from signature.repositories.cadena_repository import CadenaRepository
from signature.workers.document_consumer import _handle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT = "demo"
TRACKING = "2024-DEMO-E-000001"
DOC_UUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _make_conn(update_count: int = 2, fetchrow_return: object = None) -> AsyncMock:
    """Devuelve un AsyncMock de conexión.

    ``update_count`` controla el tag devuelto por ``conn.execute`` ("UPDATE <n>").
    ``fetchrow_return`` controla lo que devuelve ``conn.fetchrow``:
      - ``{"event_id": "..."}`` → INSERT real (primera vez)
      - ``None`` → ON CONFLICT, no-op (segunda vez, idempotente)
      - ``asyncpg.SomeError`` como side_effect → error de BD
    """
    conn = AsyncMock()
    conn.execute.return_value = f"UPDATE {update_count}"
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchrow.return_value = fetchrow_return
    return conn


def _reclassified_fields(
    *,
    tenant_slug: str = TENANT,
    tracking_number: str | None = TRACKING,
    doc_id: str | None = DOC_UUID,
    nivel_nuevo: int = 3,
    nivel_anterior: int = 1,
    event_type: str = "document.radicado.reclassified",
) -> dict[str, str]:
    """Construye los campos planos del bus para un evento reclassified."""
    payload: dict = {}
    if tracking_number is not None:
        payload["tracking_number"] = tracking_number
    if doc_id is not None:
        payload["id"] = doc_id
    payload["nivel_nuevo"] = nivel_nuevo
    payload["nivel_anterior"] = nivel_anterior

    return events.to_fields(
        events.build_event(
            event_type,
            tenant_slug=tenant_slug,
            payload=payload,
        )
    )


def _fake_acquire_tenant(conn: AsyncMock):
    """Factory: devuelve un asynccontextmanager que produce la conn dada."""

    @asynccontextmanager
    async def _ctx(tenant_slug: str):  # noqa: ARG001
        yield conn

    return _ctx


# ===========================================================================
# 1. CadenaRepository.actualizar_nivel_seguridad
# ===========================================================================


@pytest.mark.asyncio
async def test_actualizar_nivel_seguridad_devuelve_filas_afectadas() -> None:
    """execute → "UPDATE 2" → el método parsea el tag y devuelve 2."""
    conn = _make_conn(update_count=2)
    repo = CadenaRepository(conn)

    resultado = await repo.actualizar_nivel_seguridad(
        "radicado", [TRACKING, DOC_UUID], nivel=3
    )

    assert resultado == 2


@pytest.mark.asyncio
async def test_actualizar_nivel_seguridad_construye_sql_correcto() -> None:
    """Verifica objeto_tipo='radicado', ANY con ambos ids y nivel=$1."""
    conn = _make_conn(update_count=2)
    repo = CadenaRepository(conn)

    await repo.actualizar_nivel_seguridad("radicado", [TRACKING, DOC_UUID], nivel=3)

    conn.execute.assert_called_once()
    args = conn.execute.call_args.args
    sql = args[0]

    # Parámetros posicionales: ($1=nivel, $2=objeto_tipo, $3=lista_ids)
    assert "firma_solicitud" in sql
    assert "nivel_seguridad" in sql
    assert "ANY" in sql
    assert args[1] == 3         # $1 = nivel
    assert args[2] == "radicado"  # $2 = objeto_tipo
    assert args[3] == [TRACKING, DOC_UUID]  # $3 = objeto_ids


@pytest.mark.asyncio
async def test_actualizar_nivel_seguridad_zero_cuando_no_hay_cambio() -> None:
    """execute → "UPDATE 0" (guard <> $1 activo) → devuelve 0."""
    conn = _make_conn(update_count=0)
    repo = CadenaRepository(conn)

    resultado = await repo.actualizar_nivel_seguridad("radicado", [TRACKING], nivel=3)

    assert resultado == 0


# ===========================================================================
# 2. _handle — consumidor document_consumer
# ===========================================================================


@pytest.mark.asyncio
async def test_handle_reclassified_llama_actualizar_con_args_correctos() -> None:
    """_handle con evento reclassified válido → actualizar_nivel_seguridad invocado."""
    conn = _make_conn(update_count=1)
    mock_audit = AsyncMock()

    data = _reclassified_fields(nivel_nuevo=3)

    with (
        patch("signature.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("signature.workers.document_consumer._audit", mock_audit),
        patch("signature.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _handle(data)

    # actualizar_nivel_seguridad llama a conn.execute una vez.
    conn.execute.assert_called_once()
    args = conn.execute.call_args.args
    assert args[1] == 3           # nivel
    assert args[2] == "radicado"  # objeto_tipo
    # objeto_ids es un conjunto deduplicado; al menos uno de los dos ids está presente.
    assert TRACKING in args[3] or DOC_UUID in args[3]


@pytest.mark.asyncio
async def test_handle_reclassified_llama_audit_cuando_filas_mayor_cero() -> None:
    """n > 0 → _audit.append invocado con action="firma.solicitud_reclasificada"."""
    conn = _make_conn(update_count=1)
    mock_audit = AsyncMock()

    data = _reclassified_fields(nivel_nuevo=2)

    with (
        patch("signature.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("signature.workers.document_consumer._audit", mock_audit),
        patch("signature.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _handle(data)

    mock_audit.append.assert_called_once()
    call_kw = mock_audit.append.call_args.kwargs
    assert call_kw["action"] == "firma.solicitud_reclasificada"
    assert call_kw["tenant_slug"] == TENANT


@pytest.mark.asyncio
async def test_handle_ignora_eventos_de_otro_tipo() -> None:
    """event_type != 'document.radicado.reclassified' → acquire_tenant NO se llama."""
    data = _reclassified_fields(event_type="document.radicado.created")

    with patch("signature.workers.document_consumer.acquire_tenant") as mock_acq:
        await _handle(data)

    mock_acq.assert_not_called()


@pytest.mark.asyncio
async def test_handle_ignora_payload_sin_nivel_nuevo() -> None:
    """Payload sin nivel_nuevo → acquire_tenant NO se llama (guard temprano)."""
    # Construimos el sobre directamente sin nivel_nuevo en el payload.
    evt = events.build_event(
        "document.radicado.reclassified",
        tenant_slug=TENANT,
        payload={"tracking_number": TRACKING, "id": DOC_UUID},  # no nivel_nuevo
    )
    data = events.to_fields(evt)

    with patch("signature.workers.document_consumer.acquire_tenant") as mock_acq:
        await _handle(data)

    mock_acq.assert_not_called()


@pytest.mark.asyncio
async def test_handle_ignora_payload_sin_tracking_ni_id() -> None:
    """Payload sin tracking_number ni id → acquire_tenant NO se llama (lista vacía)."""
    evt = events.build_event(
        "document.radicado.reclassified",
        tenant_slug=TENANT,
        payload={"nivel_nuevo": 3},  # ni tracking ni id
    )
    data = events.to_fields(evt)

    with patch("signature.workers.document_consumer.acquire_tenant") as mock_acq:
        await _handle(data)

    mock_acq.assert_not_called()


@pytest.mark.asyncio
async def test_handle_ignora_payload_sin_tenant_slug() -> None:
    """Sin tenant_slug en el sobre → acquire_tenant NO se llama."""
    # to_fields usa tenant_slug="" lo cual produce un sobre con tenant_slug vacío.
    evt = events.build_event(
        "document.radicado.reclassified",
        tenant_slug="",   # vacío → envelope.tenant_slug será ""
        payload={"nivel_nuevo": 3, "tracking_number": TRACKING, "id": DOC_UUID},
    )
    data = events.to_fields(evt)

    with patch("signature.workers.document_consumer.acquire_tenant") as mock_acq:
        await _handle(data)

    mock_acq.assert_not_called()


@pytest.mark.asyncio
async def test_handle_noop_no_llama_audit_cuando_filas_es_cero() -> None:
    """n == 0 (todas las solicitudes ya tenían ese nivel) → _audit.append NO se llama.

    La idempotencia del guard '<> $1' produce 0 filas actualizadas;
    re-auditar sería ruido sin cambio real.
    """
    conn = _make_conn(update_count=0)
    mock_audit = AsyncMock()

    data = _reclassified_fields(nivel_nuevo=3)

    with (
        patch("signature.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("signature.workers.document_consumer._audit", mock_audit),
        patch("signature.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _handle(data)

    mock_audit.append.assert_not_called()


# ===========================================================================
# 3. actualizar_nivel_seguridad — UndefinedTableError (A-1)
# ===========================================================================


@pytest.mark.asyncio
async def test_actualizar_nivel_seguridad_retorna_cero_si_tabla_no_existe() -> None:
    """UndefinedTableError → firma_solicitud ausente en el tenant → retorna 0 (no-op ackeable).

    El tenant no tiene tablas de firma — no hay cadenas que sincronizar.
    El evento debe ackearse (no re-encolarse) porque el error no es transitorio.
    """
    conn = _make_conn()
    conn.execute.side_effect = asyncpg.UndefinedTableError
    repo = CadenaRepository(conn)

    resultado = await repo.actualizar_nivel_seguridad("radicado", [TRACKING], nivel=3)

    assert resultado == 0


@pytest.mark.asyncio
async def test_handle_completa_como_noop_cuando_tabla_firma_no_existe() -> None:
    """UndefinedTableError en actualizar_nivel_seguridad → _handle termina sin excepción.

    El evento queda ackeable porque el error indica que el tenant no tiene tablas
    de firma — es un no-op permanente, no un fallo transitorio.
    El audit NOT es llamado (n=0, igual que el caso idempotente normal).
    """
    conn = _make_conn()
    conn.execute.side_effect = asyncpg.UndefinedTableError
    mock_audit = AsyncMock()

    data = _reclassified_fields(nivel_nuevo=3)

    with (
        patch("signature.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("signature.workers.document_consumer._audit", mock_audit),
        patch("signature.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        # No debe lanzar — UndefinedTableError es capturado dentro de actualizar_nivel_seguridad.
        await _handle(data)

    mock_audit.append.assert_not_called()


# ===========================================================================
# 4. run() — delega en el bus en PostgreSQL (ADR-049)
# ===========================================================================
#
# Confirmar solo si `_handle` tuvo éxito, la reclamación y el dead-letter los
# hace `docuboto.core.bus` / `events.reclaim_stale` (probados contra Postgres
# real en tests/document/integration/test_bus_eventos_integration.py). Aquí,
# que el worker le entrega SU handler y SU callback de dead-letter.


@pytest.mark.asyncio
async def test_run_entrega_su_handler_y_su_dead_letter_al_bus() -> None:
    from signature.workers import document_consumer

    consumir = AsyncMock()
    with patch("signature.workers.document_consumer.bus.consumir", consumir):
        await document_consumer.run()

    consumir.assert_awaited_once()
    kwargs = consumir.await_args.kwargs
    assert kwargs["group"] == document_consumer.GROUP
    assert kwargs["streams"] == ("orpycamcp.document.events",)
    assert kwargs["handler"] is document_consumer._handle
    assert kwargs["on_dead_letter"] is document_consumer._persist_dead_letter
    assert kwargs["status_name"] == "signature"


@pytest.mark.asyncio
async def test_persist_dead_letter_inserta_y_audita() -> None:
    """R-4/ADR-021: primera llamada → INSERT real → audit.append invocado en la misma tx.

    Verifica:
    - DeadLetterRepository.insert_if_new es llamado (vía conn.fetchrow).
    - Si fetchrow devuelve una fila → audit.append se llama con actor='system'.
    - El payload de auditoría tiene las claves 'failure', 'envelope' y 'business'.
    """
    from signature.workers.document_consumer import _persist_dead_letter

    # fetchrow devuelve una fila → INSERT real (primera vez).
    row_mock = {"event_id": "eeeeeeee-0000-0000-0000-000000000001"}
    conn = _make_conn(fetchrow_return=row_mock)
    mock_audit = AsyncMock()

    envelope = {
        "event_id": "eeeeeeee-0000-0000-0000-000000000001",
        "event_type": "document.radicado.reclassified",
        "correlation_id": "cccc-0001",
        "tenant_slug": TENANT,
        "occurred_at": "2026-06-29T10:00:00+00:00",
        "actor": "user-original-42",
        "payload": {
            "tracking_number": TRACKING,
            "id": DOC_UUID,
            "nivel_nuevo": 3,
            "nivel_anterior": 1,
        },
    }
    failure_meta = {
        "dead_letter_reason": "max_deliveries_exceeded",
        "dead_letter_origin_stream": "orpycamcp.document.events",
        "dead_letter_group": "signature-service",
        "dead_letter_consumer": "signature-service-1",
        "dead_letter_delivery_count": "5",
        "dead_lettered_at": "2026-06-29T10:01:00+00:00",
        "dead_letter_last_error": "connection refused",
    }

    with (
        patch("signature.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("signature.workers.document_consumer._audit", mock_audit),
        patch("signature.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _persist_dead_letter(envelope, failure_meta)

    # INSERT fue llamado (via fetchrow en DeadLetterRepository).
    conn.fetchrow.assert_called_once()

    # audit.append fue invocado dentro de la misma transacción.
    mock_audit.append.assert_called_once()
    kw = mock_audit.append.call_args.kwargs

    # R-9: el actor del acto es "system" (el worker), no el usuario original.
    assert kw["actor"] == "system"
    assert kw["canal"] == "system"
    assert kw["action"] == "evento.dead_letter"
    assert kw["object_ref"] == "eeeeeeee-0000-0000-0000-000000000001"

    payload = kw["payload"]

    # "failure" contiene el failure_meta completo (incluyendo last_error).
    assert payload["failure"]["dead_letter_last_error"] == "connection refused"
    assert payload["failure"]["dead_letter_delivery_count"] == "5"

    # "envelope" preserva el sobre íntegro, incluyendo el actor original.
    assert payload["envelope"]["actor"] == "user-original-42"
    assert payload["envelope"]["event_type"] == "document.radicado.reclassified"
    assert payload["envelope"]["correlation_id"] == "cccc-0001"
    assert payload["envelope"]["payload"]["tracking_number"] == TRACKING

    # "business" expone los identificadores de negocio para consultas rápidas.
    assert payload["business"]["tracking_number"] == TRACKING
    assert payload["business"]["doc_id"] == DOC_UUID


@pytest.mark.asyncio
async def test_persist_dead_letter_idempotente_no_audita_en_conflict() -> None:
    """R-4 idempotencia: ON CONFLICT (event_id) → fetchrow devuelve None → audit NO se llama.

    La segunda entrega del mismo evento (at-least-once, XACK falló) produce un
    ON CONFLICT DO NOTHING.  El INSERT no devuelve fila → audit_log NO se escribe
    de nuevo → no hay doble traza.
    """
    from signature.workers.document_consumer import _persist_dead_letter

    # fetchrow devuelve None → ON CONFLICT, no-op.
    conn = _make_conn(fetchrow_return=None)
    mock_audit = AsyncMock()

    envelope = {
        "event_id": "eeeeeeee-0000-0000-0000-000000000001",
        "event_type": "document.radicado.reclassified",
        "tenant_slug": TENANT,
        "actor": "user-1",
        "payload": {"tracking_number": TRACKING},
    }
    failure_meta = {"dead_letter_reason": "max_deliveries_exceeded"}

    with (
        patch("signature.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)),
        patch("signature.workers.document_consumer._audit", mock_audit),
        patch("signature.workers.document_consumer._AUDIT_AVAILABLE", True),
    ):
        await _persist_dead_letter(envelope, failure_meta)

    # fetchrow fue llamado (el INSERT se intentó).
    conn.fetchrow.assert_called_once()
    # Pero NO se auditó (ON CONFLICT → sin fila devuelta).
    mock_audit.append.assert_not_called()


@pytest.mark.asyncio
async def test_persist_dead_letter_error_transiente_propaga() -> None:
    """R-4: error transitorio de BD en fetchrow → excepción propaga (no se traga).

    El helper reclaim_stale interpretará la excepción como callback fallido y
    NO ackeará → el mensaje permanece en PEL para reintento.
    """
    from signature.workers.document_consumer import _persist_dead_letter

    conn = _make_conn()
    conn.fetchrow.side_effect = OSError("connection reset by peer")

    envelope = {
        "event_id": "eeeeeeee-0000-0000-0000-000000000002",
        "event_type": "document.radicado.reclassified",
        "tenant_slug": TENANT,
        "actor": "user-1",
        "payload": {"tracking_number": TRACKING},
    }
    failure_meta = {"dead_letter_reason": "max_deliveries_exceeded"}

    with patch("signature.workers.document_consumer.acquire_tenant", _fake_acquire_tenant(conn)):
        with pytest.raises(OSError, match="connection reset by peer"):
            await _persist_dead_letter(envelope, failure_meta)


@pytest.mark.asyncio
async def test_persist_dead_letter_sin_tenant_slug_retorna_sin_error() -> None:
    """Evento malformado permanente (sin tenant_slug) → retorna normalmente → XACK permitido.

    No se puede escribir a ningún schema sin tenant_slug; reintentar crearía un loop
    infinito.  La función retorna normalmente (no lanza) para que el helper pueda
    XACK el poison pill y liberar la PEL.
    """
    from signature.workers.document_consumer import _persist_dead_letter

    mock_audit = AsyncMock()
    envelope = {"event_id": "abc", "actor": "user-1", "payload": {}}  # sin tenant_slug
    failure_meta = {"dead_letter_reason": "max_deliveries_exceeded"}

    with (
        patch("signature.workers.document_consumer._audit", mock_audit),
        patch("signature.workers.document_consumer._AUDIT_AVAILABLE", True),
        patch("signature.workers.document_consumer.acquire_tenant") as mock_acq,
    ):
        await _persist_dead_letter(envelope, failure_meta)  # no debe lanzar

    # acquire_tenant nunca fue invocado — no hay schema destino.
    mock_acq.assert_not_called()
    mock_audit.append.assert_not_called()
