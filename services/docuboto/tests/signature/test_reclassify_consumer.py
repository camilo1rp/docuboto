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
  3. run() — se engancha al bus con su handler y su dead-letter. La escritura del
     aparcamiento es la única (`docuboto.core.dead_letter.persistir`) y se prueba en
     `tests/docuboto/test_dead_letter_persistir.py`.

Patrón de mock:
  - acquire_tenant se parchea como asynccontextmanager que devuelve un AsyncMock.
  - _audit se parchea a nivel de módulo (app.workers.document_consumer._audit)
    con un AsyncMock para .append.
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
    # La retención de sus dead-letters la hace el barrido para todos los grupos.
    assert "periodico" not in kwargs
