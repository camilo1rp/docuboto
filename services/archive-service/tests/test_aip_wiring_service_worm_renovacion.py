# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""E10 debt — renovación de retención WORM del AIP OAIS para series de
disposición Conservación Total (CT): `AipWiringService._resolve_ct_retencion_anios`
(total TRD, versión de renovación) y `AipWiringService.renovar_worm_aip`
(disparo best-effort, marca + asiento, backoff/agotamiento) -- espejo EXACTO
de `test_index_service_worm_renovacion.py` (índice) para el mismo eje de
renovación, aplicado al AIP (`expediente_aip`, migración 034, fast-follow del
cableado del AIP).

Todo pure-unit (conexión mockeada) -- el filtro CT en SQL de
`AipRepository.list_worm_renovacion_pending` (fail-closed estructural) SOLO
se prueba con Postgres real, ver
`tests/integration/test_worm_renovacion_ct_filter.py`.

Diferencia deliberada frente al índice: `AipWiringService.renovar_worm_aip`
NO recibe un `indice_id` separado -- `expediente_aip` tiene PK
`expediente_id` (una fila por expediente), así que `mark_worm_renovado`/
`bump_worm_renovacion_intento` solo necesitan `expediente_id`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_client import WormRenewalOutcome
from app.core.config import settings
from app.services.aip_wiring_service import AipWiringService

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
async def test_renovar_worm_aip_trd_sin_retencion_uses_backoff() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(return_value=None)
    expediente_id = uuid.uuid4()

    resultado = await service.renovar_worm_aip(expediente_id, intentos_previos=0)

    assert resultado == "pendiente"
    bump_calls = _bump_calls(conn)
    assert len(bump_calls) == 1
    # params: (expediente_id, proximo_at, ultimo_error, agotado)
    assert bump_calls[0].args[3] == "trd_sin_retencion"
    assert bump_calls[0].args[4] is False
    assert bump_calls[0].args[2] is not None  # proximo_at programado
    assert _renovado_update_calls(conn) == []


# ---------------------------------------------------------------------------
# Extensión CONFIRMADA (outcome.renovado=True) -- marca el espejo Y audita.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_aip_success_marks_and_audits_when_renovado() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(return_value=25)  # serie CT: total TRD real
    expediente_id = uuid.uuid4()
    nueva = datetime(2051, 1, 1, tzinfo=UTC)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
    ) as mock_client:
        resultado = await service.renovar_worm_aip(expediente_id, intentos_previos=0)

    assert resultado == "renovado"
    mock_client.assert_awaited_once()
    assert mock_client.await_args.kwargs["tenant_slug"] == TENANT
    assert mock_client.await_args.kwargs["expediente_id"] == str(expediente_id)
    assert mock_client.await_args.kwargs["tipo"] == "aip"
    assert mock_client.await_args.kwargs["retencion_anios"] == 25
    # ventana RODANTE: `fecha_inicio=hoy`, NUNCA una fecha lejana/9999.
    assert mock_client.await_args.kwargs["fecha_inicio"] == datetime.now(tz=UTC).date()

    mark_calls = _renovado_update_calls(conn)
    assert len(mark_calls) == 1
    assert mark_calls[0].args[1:] == (expediente_id, nueva)

    audit_calls = _audit_calls(conn)
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "archive.aip_worm_retencion_renovada"
    assert audit_calls[0].args[6] == "expediente_aip"


# ---------------------------------------------------------------------------
# Llamada CONFIRMADA pero no-op (outcome.renovado=False) -- marca el espejo
# (deja la fila sana), pero SIN asiento fantasma sobre una extensión que NO
# ocurrió realmente.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_aip_confirmed_noop_marks_but_does_not_audit() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(return_value=25)
    expediente_id = uuid.uuid4()
    vigente = datetime(2051, 1, 1, tzinfo=UTC)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(
            return_value=WormRenewalOutcome(ok=True, retain_until=vigente, renovado=False)
        ),
    ):
        resultado = await service.renovar_worm_aip(expediente_id, intentos_previos=0)

    assert resultado == "omitido"
    assert len(_renovado_update_calls(conn)) == 1  # espejo se actualiza igual (resetea backoff)
    assert _audit_calls(conn) == []  # NADA que auditar: no hubo extensión real


# ---------------------------------------------------------------------------
# Fallos transitorios/estructurales de storage-service -- mismo backoff que
# la protección inicial, motivo distinto según la causa.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_aip_disabled_schedules_backoff() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(return_value=25)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, disabled=True)),
    ):
        resultado = await service.renovar_worm_aip(uuid.uuid4(), intentos_previos=0)

    assert resultado == "pendiente"
    assert _bump_calls(conn)[0].args[3] == "worm_disabled"


@pytest.mark.asyncio
async def test_renovar_worm_aip_not_found_schedules_backoff() -> None:
    """`worm_object_not_found` (404 de storage-service): el AIP nunca se
    empaquetó bajo WORM en primer lugar -- no debería ocurrir en la práctica
    (`list_worm_renovacion_pending` solo lista `estado='empaquetado'`), pero
    el motivo se distingue igual para diagnóstico operacional."""
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(return_value=25)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, not_found=True)),
    ):
        resultado = await service.renovar_worm_aip(uuid.uuid4(), intentos_previos=0)

    assert resultado == "pendiente"
    assert _bump_calls(conn)[0].args[3] == "worm_object_not_found"


@pytest.mark.asyncio
async def test_renovar_worm_aip_transient_failure_marks_worm_no_confirmado() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(return_value=25)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, transient=True)),
    ):
        resultado = await service.renovar_worm_aip(uuid.uuid4(), intentos_previos=0)

    assert resultado == "pendiente"
    assert _bump_calls(conn)[0].args[3] == "worm_no_confirmado"


# ---------------------------------------------------------------------------
# Agotamiento -- alarma operacional MÁS SERIA que en el eje de protección:
# una serie CT podría perder su lock COMPLIANCE.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_aip_exhausts_after_max_intentos_and_audits() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(return_value=25)
    max_intentos = settings.indice_reconciliation_max_intentos

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, transient=True)),
    ):
        resultado = await service.renovar_worm_aip(
            uuid.uuid4(), intentos_previos=max_intentos - 1
        )

    assert resultado == "agotado"
    bump_calls = _bump_calls(conn)
    assert bump_calls[0].args[4] is True  # agotado
    assert bump_calls[0].args[2] is None  # sin próximo intento programado

    audit_calls = _audit_calls(conn)
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "archive.aip_worm_renovacion_agotado"


# ---------------------------------------------------------------------------
# Best-effort real: NUNCA propaga una excepción no prevista.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_worm_aip_never_raises_on_unexpected_exception() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    service._resolve_ct_retencion_anios = AsyncMock(side_effect=RuntimeError("boom (itest)"))

    resultado = await service.renovar_worm_aip(uuid.uuid4(), intentos_previos=0)

    assert resultado == "pendiente"


# ---------------------------------------------------------------------------
# `_resolve_ct_retencion_anios` -- total TRD (ambas fases), SIN exigir
# `closed_at` (a diferencia de `_resolve_aip_retention`, usado al empaquetar,
# la ventana rodante de renovación siempre usa `fecha_inicio=hoy`).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_ct_retencion_anios_sums_gestion_and_central_years() -> None:
    from tests.conftest import make_expediente_record, make_trd_serie_record

    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    serie_id = uuid.uuid4()
    expediente_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=expediente_id, trd_serie_id=serie_id, exp_status="closed"),
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=10, archivo_central_years=15),
    ]

    assert await service._resolve_ct_retencion_anios(expediente_id) == 25


@pytest.mark.asyncio
async def test_resolve_ct_retencion_anios_none_when_total_out_of_range() -> None:
    """Total fuera de `[1, 100]` (mismo piso/techo que `total_trd_years`) --
    fail-closed, NUNCA renueva con un total inválido."""
    from tests.conftest import make_expediente_record, make_trd_serie_record

    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    serie_id = uuid.uuid4()
    expediente_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=expediente_id, trd_serie_id=serie_id, exp_status="closed"),
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=60, archivo_central_years=60),
    ]

    assert await service._resolve_ct_retencion_anios(expediente_id) is None


@pytest.mark.asyncio
async def test_resolve_ct_retencion_anios_none_when_expediente_missing_trd_serie() -> None:
    conn = _mock_conn()
    service = AipWiringService(conn, TENANT)
    conn.fetchrow.side_effect = [None]  # expediente inexistente/oculto por clearance

    assert await service._resolve_ct_retencion_anios(uuid.uuid4()) is None
