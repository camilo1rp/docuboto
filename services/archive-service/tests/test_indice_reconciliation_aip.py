# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""6ª fase de reconciliación — cableado del AIP OAIS al cierre del expediente
(E10 debt "AIP sin-wiring"): `app/jobs/indice_reconciliation.py::run_once_aip`/
`_reconciliar_aip`.

Pure-unit (pool/conexiones mockeados, `AipWiringService.intentar_empaquetar`
mockeado) — mismo estilo que `test_indice_reconciliation_worm.py` para la
fase del cableado WORM del índice. La lógica de negocio de
`intentar_empaquetar` en sí ya está cubierta en `test_aip_wiring_service.py`
(unit) y `tests/integration/test_aip_wiring_atomicity.py` (Postgres real);
aquí solo se prueba que el job BARRE, aísla fallos por-expediente, respeta el
advisory lock PROPIO (`aip_reconcile`, distinto de `indice_reconcile`/
`worm_reconcile`/...) y agrega el resumen correctamente.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.jobs import indice_reconciliation


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _aip_pending_row(**overrides) -> dict:
    base = {
        "expediente_id": uuid.uuid4(),
        "expediente_code": "EXP-A-0001",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_reconciliar_aip_delegates_to_wiring_service() -> None:
    conn = _mock_conn()
    row = _aip_pending_row()

    with patch(
        "app.jobs.indice_reconciliation.AipWiringService.intentar_empaquetar",
        new=AsyncMock(return_value="empaquetado"),
    ) as mock_intentar:
        resultado = await indice_reconciliation._reconciliar_aip(conn, "demo", row)

    assert resultado == "empaquetado"
    mock_intentar.assert_awaited_once_with(row["expediente_id"])


@pytest.mark.asyncio
async def test_run_once_aip_aggregates_across_tenants_and_respects_advisory_lock() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}, {"slug": "tenant-b"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True  # pg_try_advisory_lock -> adquirido
    row_a = _aip_pending_row()
    conn_a.fetch.return_value = [row_a]

    conn_b = _mock_conn()
    conn_b.fetchval.return_value = False  # otra instancia ya sostiene el lock

    conns = {"tenant-a": conn_a, "tenant-b": conn_b}

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conns[slug]

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.intentar_empaquetar",
            new=AsyncMock(return_value="empaquetado"),
        ),
    ):
        summary = await indice_reconciliation.run_once_aip()

    assert summary["pendientes_vistos"] == 1
    assert summary["empaquetados"] == 1
    assert summary["omitidos"] == 0
    assert summary["fallidos"] == 0
    assert summary["agotados"] == 0
    conn_b.fetch.assert_not_called()

    # El advisory lock que se probó/soltó es el namespace PROPIO de esta
    # fase (`aip_reconcile`), NUNCA el de índice/acta/worm/renovación.
    lock_calls = [
        c for c in conn_a.fetchval.call_args_list if c.args and "pg_try_advisory_lock" in c.args[0]
    ]
    assert len(lock_calls) == 1
    assert "orpycamcp:archive:aip_reconcile" in lock_calls[0].args[0]
    unlock_calls = [
        c for c in conn_a.execute.call_args_list if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(unlock_calls) == 1
    assert "orpycamcp:archive:aip_reconcile" in unlock_calls[0].args[0]


@pytest.mark.asyncio
async def test_run_once_aip_isolates_per_expediente_failure() -> None:
    """Un expediente que revienta con una excepción inesperada NO aborta la
    barrida del resto del batch de ese tenant."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_boom = _aip_pending_row(expediente_code="EXP-A-BOOM")
    row_ok = _aip_pending_row(expediente_code="EXP-A-OK")
    conn_a.fetch.return_value = [row_boom, row_ok]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    async def _side_effect(expediente_id):
        if expediente_id == row_boom["expediente_id"]:
            raise RuntimeError("boom (itest)")
        return "empaquetado"

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.intentar_empaquetar",
            new=AsyncMock(side_effect=_side_effect),
        ),
    ):
        summary = await indice_reconciliation.run_once_aip()

    assert summary["pendientes_vistos"] == 2
    assert summary["empaquetados"] == 1
    assert summary["fallidos"] == 1


@pytest.mark.asyncio
async def test_run_once_aip_counts_omitido_outcome() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    conn_a.fetch.return_value = [_aip_pending_row()]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.intentar_empaquetar",
            new=AsyncMock(return_value="omitido"),
        ),
    ):
        summary = await indice_reconciliation.run_once_aip()

    assert summary["omitidos"] == 1
    assert summary["empaquetados"] == 0
    assert summary["fallidos"] == 0


@pytest.mark.asyncio
async def test_run_once_aip_counts_agotado_outcome() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    conn_a.fetch.return_value = [_aip_pending_row()]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.intentar_empaquetar",
            new=AsyncMock(return_value="agotado"),
        ),
    ):
        summary = await indice_reconciliation.run_once_aip()

    assert summary["agotados"] == 1
    assert summary["empaquetados"] == 0
    assert summary["fallidos"] == 0


@pytest.mark.asyncio
async def test_run_once_aip_counts_pendiente_outcome_as_fallido() -> None:
    """`'pendiente'` (backoff programado, sin excepción) no tiene rama
    explícita en el `if/elif` del summary — cae en el `else` y se contabiliza
    como `fallidos`, MISMO criterio que `run_once_worm`. No es un bug nuevo
    de esta fase: se deja documentado aquí para que un futuro refinamiento
    del summary (p. ej. una clave `pendientes` separada) no lo rompa por
    sorpresa — ver hallazgo en el reporte de esta suite."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    conn_a.fetch.return_value = [_aip_pending_row()]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.AipWiringService.intentar_empaquetar",
            new=AsyncMock(return_value="pendiente"),
        ),
    ):
        summary = await indice_reconciliation.run_once_aip()

    assert summary["fallidos"] == 1
