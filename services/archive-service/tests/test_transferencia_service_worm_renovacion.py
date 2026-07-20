# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""E10 debt — renovación de retención WORM del acta de transferencia para
series de disposición Conservación Total (CT):
`TransferenciaService._resolve_ct_retencion_anios_acta` (total TRD del
expediente, versión de renovación) y `TransferenciaService.renovar_worm_acta`
(disparo best-effort, marca + asiento, backoff/agotamiento) -- espejo EXACTO
de `test_index_service_worm_renovacion.py` (índice) para su gemela del acta.

Todo pure-unit (conexión mockeada) -- el filtro CT en SQL de
`TransferenciaRepository.list_worm_renovacion_pending` (fail-closed
estructural) SOLO se prueba con Postgres real, ver
`tests/integration/test_worm_renovacion_ct_filter.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_client import WormRenewalOutcome
from app.core.config import settings
from app.services.transferencia_service import TransferenciaService

TENANT = "demo"


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _renovado_update_calls(conn: AsyncMock) -> list:
    return [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_renovacion_intentos = 0" in c.args[0]
    ]


def _bump_calls(conn: AsyncMock) -> list:
    return [
        c
        for c in conn.execute.call_args_list
        if c.args and "worm_renovacion_intentos = worm_renovacion_intentos + 1" in c.args[0]
    ]


def _audit_calls(conn: AsyncMock) -> list:
    return [
        c
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# TRD sin retención resoluble -- backoff, NUNCA se inventa una retención.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_acta_trd_sin_retencion_uses_backoff() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(return_value=None)
    transferencia_id, expediente_id = uuid.uuid4(), uuid.uuid4()

    resultado = await service.renovar_worm_acta(
        transferencia_id, expediente_id, intentos_previos=0
    )

    assert resultado == "pendiente"
    bump_calls = _bump_calls(conn)
    assert len(bump_calls) == 1
    assert bump_calls[0].args[3] == "trd_sin_retencion"
    assert bump_calls[0].args[4] is False
    assert _renovado_update_calls(conn) == []


# ---------------------------------------------------------------------------
# Extensión CONFIRMADA (outcome.renovado=True) -- marca el espejo Y audita.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_acta_success_marks_and_audits_when_renovado() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(return_value=25)
    transferencia_id, expediente_id = uuid.uuid4(), uuid.uuid4()
    nueva = datetime(2051, 1, 1, tzinfo=UTC)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
    ) as mock_client:
        resultado = await service.renovar_worm_acta(
            transferencia_id, expediente_id, intentos_previos=0
        )

    assert resultado == "renovado"
    mock_client.assert_awaited_once()
    assert mock_client.await_args.kwargs["tenant_slug"] == TENANT
    assert mock_client.await_args.kwargs["expediente_id"] == str(expediente_id)
    assert mock_client.await_args.kwargs["tipo"] == "acta_transferencia"
    assert mock_client.await_args.kwargs["retencion_anios"] == 25
    assert mock_client.await_args.kwargs["fecha_inicio"] == datetime.now(tz=UTC).date()

    mark_calls = _renovado_update_calls(conn)
    assert len(mark_calls) == 1
    assert mark_calls[0].args[1:] == (transferencia_id, nueva)

    audit_calls = _audit_calls(conn)
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "archive.acta_worm_retencion_renovada"
    assert audit_calls[0].args[6] == "transferencia_acta"


# ---------------------------------------------------------------------------
# Llamada CONFIRMADA pero no-op (outcome.renovado=False) -- marca el espejo
# pero SIN asiento fantasma.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_acta_confirmed_noop_marks_but_does_not_audit() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(return_value=25)
    transferencia_id, expediente_id = uuid.uuid4(), uuid.uuid4()
    vigente = datetime(2051, 1, 1, tzinfo=UTC)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(
            return_value=WormRenewalOutcome(ok=True, retain_until=vigente, renovado=False)
        ),
    ):
        resultado = await service.renovar_worm_acta(
            transferencia_id, expediente_id, intentos_previos=0
        )

    assert resultado == "omitido"
    assert len(_renovado_update_calls(conn)) == 1
    assert _audit_calls(conn) == []


# ---------------------------------------------------------------------------
# Fallos transitorios/estructurales de storage-service.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_acta_disabled_schedules_backoff() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(return_value=25)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, disabled=True)),
    ):
        resultado = await service.renovar_worm_acta(
            uuid.uuid4(), uuid.uuid4(), intentos_previos=0
        )

    assert resultado == "pendiente"
    assert _bump_calls(conn)[0].args[3] == "worm_disabled"


@pytest.mark.asyncio
async def test_renovar_worm_acta_not_found_schedules_backoff() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(return_value=25)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, not_found=True)),
    ):
        resultado = await service.renovar_worm_acta(
            uuid.uuid4(), uuid.uuid4(), intentos_previos=0
        )

    assert resultado == "pendiente"
    assert _bump_calls(conn)[0].args[3] == "worm_object_not_found"


@pytest.mark.asyncio
async def test_renovar_worm_acta_transient_failure_marks_worm_no_confirmado() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(return_value=25)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, transient=True)),
    ):
        resultado = await service.renovar_worm_acta(
            uuid.uuid4(), uuid.uuid4(), intentos_previos=0
        )

    assert resultado == "pendiente"
    assert _bump_calls(conn)[0].args[3] == "worm_no_confirmado"


# ---------------------------------------------------------------------------
# Agotamiento -- alarma operacional URGENTE (serie CT podría perder el lock).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_acta_exhausts_after_max_intentos_and_audits() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(return_value=25)
    max_intentos = settings.indice_reconciliation_max_intentos

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, transient=True)),
    ):
        resultado = await service.renovar_worm_acta(
            uuid.uuid4(), uuid.uuid4(), intentos_previos=max_intentos - 1
        )

    assert resultado == "agotado"
    bump_calls = _bump_calls(conn)
    assert bump_calls[0].args[4] is True
    assert bump_calls[0].args[2] is None

    audit_calls = _audit_calls(conn)
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "archive.acta_worm_renovacion_agotado"


# ---------------------------------------------------------------------------
# Best-effort real: NUNCA propaga una excepción no prevista.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_acta_never_raises_on_unexpected_exception() -> None:
    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    service._resolve_ct_retencion_anios_acta = AsyncMock(side_effect=RuntimeError("boom (itest)"))

    resultado = await service.renovar_worm_acta(
        uuid.uuid4(), uuid.uuid4(), intentos_previos=0
    )

    assert resultado == "pendiente"


# ---------------------------------------------------------------------------
# `_resolve_ct_retencion_anios_acta` -- total TRD del EXPEDIENTE (ambas
# fases), SIN exigir `firmado_at` del acta (a diferencia de
# `_resolve_worm_retention_acta`, la ventana rodante SIEMPRE usa hoy).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_ct_retencion_anios_acta_sums_gestion_and_central_years() -> None:
    from tests.conftest import make_expediente_record, make_trd_serie_record

    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    serie_id = uuid.uuid4()
    expediente_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=expediente_id, trd_serie_id=serie_id, exp_status="closed"),
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=10, archivo_central_years=15),
    ]

    assert await service._resolve_ct_retencion_anios_acta(expediente_id) == 25


@pytest.mark.asyncio
async def test_resolve_ct_retencion_anios_acta_none_when_no_trd_serie() -> None:
    from tests.conftest import make_expediente_record

    conn = _mock_conn()
    service = TransferenciaService(conn, TENANT)
    expediente_id = uuid.uuid4()
    conn.fetchrow.return_value = make_expediente_record(
        exp_id=expediente_id, trd_serie_id=None, exp_status="closed"
    )

    assert await service._resolve_ct_retencion_anios_acta(expediente_id) is None
