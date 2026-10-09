# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""5ª fase de reconciliación -- renovación de retención WORM para series de
disposición Conservación Total (E10 debt):
`app/jobs/indice_reconciliation.py::run_once_worm_renovacion`/
`_reconciliar_worm_renovacion_indice`/`_reconciliar_worm_renovacion_acta`/
`_reconciliar_worm_renovacion_aip`.
Espejo de `test_indice_reconciliation_worm.py`/`test_indice_reconciliation_acta_worm.py`
(3ra/4ta fase) para el eje de renovación -- una sola función cubre los TRES
artefactos (índice, acta Y AIP -- este último, E10 debt, fast-follow del
cableado del AIP), con TRES advisory locks POR TENANT en el MISMO namespace
de barrida (`worm_renovacion_indice`/`worm_renovacion_acta`/
`worm_renovacion_aip`).

Pure-unit (pool/conexiones mockeados, `IndexService.renovar_worm_indice`/
`TransferenciaService.renovar_worm_acta`/`AipWiringService.renovar_worm_aip`
mockeados) -- mismo estilo que las fases 3/4. La atomicidad REAL de la
marca+asiento ya está cubierta en `test_index_service_worm_renovacion.py`/
`test_transferencia_service_worm_renovacion.py`/
`test_aip_wiring_service_worm_renovacion.py` (unit) y
`tests/integration/test_worm_renovacion_ct_filter.py` (Postgres real, filtro
CT estructural); aquí solo se prueba que el job BARRE LOS TRES ejes, aísla
fallos por-artefacto, respeta los TRES advisory locks independientes y agrega
el resumen correctamente."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.disposition import CT_DISPOSITION_CODES
from app.jobs import indice_reconciliation


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _renovacion_pending_indice_row(**overrides) -> dict:
    base = {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "worm_retain_until": None,
        "worm_renovacion_intentos": 0,
        "expediente_code": "EXP-CT-0001",
    }
    base.update(overrides)
    return base


def _renovacion_pending_acta_row(**overrides) -> dict:
    base = {
        "transferencia_id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "worm_retain_until": None,
        "worm_renovacion_intentos": 0,
    }
    base.update(overrides)
    return base


def _renovacion_pending_aip_row(**overrides) -> dict:
    base = {
        "expediente_id": uuid.uuid4(),
        "worm_retain_until": None,
        "worm_renovacion_intentos": 0,
        "expediente_code": "EXP-CT-AIP-0001",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Adaptadores delgados -- delegan al servicio, pasando `intentos_previos`
# desde la fila del espejo (NO desde 0 fijo).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliar_worm_renovacion_indice_delegates_to_index_service() -> None:
    conn = _mock_conn()
    row = _renovacion_pending_indice_row(worm_renovacion_intentos=3)

    with patch(
        "app.jobs.indice_reconciliation.IndexService.renovar_worm_indice",
        new=AsyncMock(return_value="renovado"),
    ) as mock_renovar:
        resultado = await indice_reconciliation._reconciliar_worm_renovacion_indice(
            conn, "demo", row
        )

    assert resultado == "renovado"
    mock_renovar.assert_awaited_once_with(
        row["expediente_id"], row["id"], intentos_previos=3
    )


@pytest.mark.asyncio
async def test_reconciliar_worm_renovacion_acta_delegates_to_transferencia_service() -> None:
    conn = _mock_conn()
    row = _renovacion_pending_acta_row(worm_renovacion_intentos=2)

    with patch(
        "app.jobs.indice_reconciliation.TransferenciaService.renovar_worm_acta",
        new=AsyncMock(return_value="renovado"),
    ) as mock_renovar:
        resultado = await indice_reconciliation._reconciliar_worm_renovacion_acta(
            conn, "demo", row
        )

    assert resultado == "renovado"
    mock_renovar.assert_awaited_once_with(
        row["transferencia_id"], row["expediente_id"], intentos_previos=2
    )


@pytest.mark.asyncio
async def test_reconciliar_worm_renovacion_aip_delegates_to_aip_wiring_service() -> None:
    conn = _mock_conn()
    row = _renovacion_pending_aip_row(worm_renovacion_intentos=4)

    with patch(
        "app.jobs.indice_reconciliation.AipWiringService.renovar_worm_aip",
        new=AsyncMock(return_value="renovado"),
    ) as mock_renovar:
        resultado = await indice_reconciliation._reconciliar_worm_renovacion_aip(conn, "demo", row)

    assert resultado == "renovado"
    mock_renovar.assert_awaited_once_with(row["expediente_id"], intentos_previos=4)


# ---------------------------------------------------------------------------
# `run_once_worm_renovacion` -- filtro CT/ventana pasado a los TRES repositorios.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_passes_ct_codes_and_ventana_to_all_three_repos() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True  # los TRES pg_try_advisory_lock -> adquiridos

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ) as mock_list_indice,
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ) as mock_list_acta,
        patch(
            "app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ) as mock_list_aip,
    ):
        await indice_reconciliation.run_once_worm_renovacion()

    from app.core.config import settings

    mock_list_indice.assert_awaited_once_with(
        ventana_dias=settings.worm_renovacion_ventana_dias,
        ct_codes=list(CT_DISPOSITION_CODES),
        limit=settings.indice_reconciliation_batch_size,
    )
    mock_list_acta.assert_awaited_once_with(
        ventana_dias=settings.worm_renovacion_ventana_dias,
        ct_codes=list(CT_DISPOSITION_CODES),
        limit=settings.indice_reconciliation_batch_size,
    )
    mock_list_aip.assert_awaited_once_with(
        ventana_dias=settings.worm_renovacion_ventana_dias,
        ct_codes=list(CT_DISPOSITION_CODES),
        limit=settings.indice_reconciliation_batch_size,
    )


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_aggregates_across_indice_acta_and_aip() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_indice = _renovacion_pending_indice_row()
    row_acta = _renovacion_pending_acta_row()
    row_aip = _renovacion_pending_aip_row()

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_indice]),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_acta]),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_aip]),
        ),
        patch(
            "app.jobs.indice_reconciliation.IndexService.renovar_worm_indice",
            new=AsyncMock(return_value="renovado"),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaService.renovar_worm_acta",
            new=AsyncMock(return_value="renovado"),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.renovar_worm_aip",
            new=AsyncMock(return_value="renovado"),
        ),
    ):
        summary = await indice_reconciliation.run_once_worm_renovacion()

    assert summary["pendientes_vistos"] == 3  # 1 índice + 1 acta + 1 AIP
    assert summary["renovados"] == 3
    assert summary["fallidos"] == 0
    assert summary["agotados"] == 0


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_isolates_per_item_failure_on_both_axes() -> None:
    """Un artefacto (índice O acta) que revienta con una excepción inesperada
    NO aborta la barrida del resto -- ni siquiera del otro eje."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_indice_boom = _renovacion_pending_indice_row(expediente_code="EXP-BOOM")
    row_acta_ok = _renovacion_pending_acta_row()

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    async def _indice_side_effect(expediente_id, indice_id, *, intentos_previos):
        raise RuntimeError("boom (itest)")

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_indice_boom]),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_acta_ok]),
        ),
        patch(
            "app.jobs.indice_reconciliation.IndexService.renovar_worm_indice",
            new=AsyncMock(side_effect=_indice_side_effect),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaService.renovar_worm_acta",
            new=AsyncMock(return_value="renovado"),
        ),
    ):
        summary = await indice_reconciliation.run_once_worm_renovacion()

    assert summary["pendientes_vistos"] == 2
    assert summary["fallidos"] == 1
    assert summary["renovados"] == 1


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_isolates_aip_failure_from_other_two_axes() -> None:
    """Gemelo del test anterior, pero con la excepción inesperada en el eje
    del AIP -- ni el índice ni el acta (que SÍ tienen éxito) se ven afectados
    por el reventón en el tercer eje."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_indice_ok = _renovacion_pending_indice_row()
    row_aip_boom = _renovacion_pending_aip_row(expediente_code="EXP-BOOM-AIP")

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    async def _aip_side_effect(expediente_id, *, intentos_previos):
        raise RuntimeError("boom (itest)")

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_indice_ok]),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_aip_boom]),
        ),
        patch(
            "app.jobs.indice_reconciliation.IndexService.renovar_worm_indice",
            new=AsyncMock(return_value="renovado"),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.renovar_worm_aip",
            new=AsyncMock(side_effect=_aip_side_effect),
        ),
    ):
        summary = await indice_reconciliation.run_once_worm_renovacion()

    assert summary["pendientes_vistos"] == 2
    assert summary["fallidos"] == 1
    assert summary["renovados"] == 1


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_counts_agotado_outcome() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row = _renovacion_pending_indice_row(worm_renovacion_intentos=7)

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.jobs.indice_reconciliation.IndexService.renovar_worm_indice",
            new=AsyncMock(return_value="agotado"),
        ),
    ):
        summary = await indice_reconciliation.run_once_worm_renovacion()

    assert summary["agotados"] == 1
    assert summary["renovados"] == 0
    assert summary["fallidos"] == 0


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_counts_agotado_outcome_for_aip() -> None:
    """Gemelo del test anterior para el eje del AIP -- el agotamiento aquí es
    una alarma MÁS SERIA que en el eje de empaquetado (`expediente_aip.estado`
    permanece `'empaquetado'`, TERMINAL): un artefacto de conservación
    PERMANENTE podría perder su lock COMPLIANCE."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row = _renovacion_pending_aip_row(worm_renovacion_intentos=7)

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.renovar_worm_aip",
            new=AsyncMock(return_value="agotado"),
        ),
    ):
        summary = await indice_reconciliation.run_once_worm_renovacion()

    assert summary["agotados"] == 1
    assert summary["renovados"] == 0
    assert summary["fallidos"] == 0


# ---------------------------------------------------------------------------
# Advisory locks INDEPENDIENTES (`worm_renovacion_indice`/
# `worm_renovacion_acta`/`worm_renovacion_aip`) -- un eje puede saltarse su
# barrida (otra instancia ya lo sostiene) sin bloquear los otros DOS.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_indice_lock_busy_does_not_block_acta_or_aip() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    # 1er pg_try_advisory_lock (índice) -> ocupado; 2do (acta), 3ro (AIP) y 4to (firmas
    # personales del acta, AREA-07-10) -> libres.
    conn_a.fetchval.side_effect = [False, True, True, True]
    row_acta = _renovacion_pending_acta_row()

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ) as mock_list_indice,
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_acta]),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaService.renovar_worm_acta",
            new=AsyncMock(return_value="renovado"),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ) as mock_list_aip,
    ):
        summary = await indice_reconciliation.run_once_worm_renovacion()

    mock_list_indice.assert_not_awaited()  # el eje índice ni siquiera consultó
    mock_list_aip.assert_awaited_once()  # el eje AIP SÍ corrió (lock propio, libre)
    assert summary["pendientes_vistos"] == 1
    assert summary["renovados"] == 1


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_aip_lock_busy_does_not_block_indice_or_acta() -> None:
    """Gemelo del test anterior con el eje OCUPADO siendo el AIP (3er
    `pg_try_advisory_lock`, namespace `worm_renovacion_aip`) -- índice y acta
    corren con normalidad."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    # 1er (índice) -> libre; 2do (acta) -> libre; 3ro (AIP) -> ocupado; 4to (firmas) -> libre.
    conn_a.fetchval.side_effect = [True, True, False, True]
    row_indice = _renovacion_pending_indice_row()

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[row_indice]),
        ),
        patch(
            "app.jobs.indice_reconciliation.IndexService.renovar_worm_indice",
            new=AsyncMock(return_value="renovado"),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ) as mock_list_acta,
        patch(
            "app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ) as mock_list_aip,
    ):
        summary = await indice_reconciliation.run_once_worm_renovacion()

    mock_list_acta.assert_awaited_once()  # el eje acta SÍ corrió (lock propio, libre)
    mock_list_aip.assert_not_awaited()  # el eje AIP ni siquiera consultó (lock ocupado)
    assert summary["pendientes_vistos"] == 1
    assert summary["renovados"] == 1


@pytest.mark.asyncio
async def test_run_once_worm_renovacion_acquires_and_releases_all_three_locks() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await indice_reconciliation.run_once_worm_renovacion()

    unlock_indice = [
        c
        for c in conn_a.execute.call_args_list
        if c.args and "worm_renovacion_indice" in c.args[0] and "unlock" in c.args[0]
    ]
    unlock_acta = [
        c
        for c in conn_a.execute.call_args_list
        if c.args and "worm_renovacion_acta" in c.args[0] and "unlock" in c.args[0]
    ]
    unlock_aip = [
        c
        for c in conn_a.execute.call_args_list
        if c.args and "worm_renovacion_aip" in c.args[0] and "unlock" in c.args[0]
    ]
    assert len(unlock_indice) == 1
    assert len(unlock_acta) == 1
    assert len(unlock_aip) == 1
