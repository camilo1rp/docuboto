# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""4ª fase de reconciliación -- cableado WORM/Object-Lock del ACTA de
transferencia (E10 Inc.2 wiring):
`app/jobs/indice_reconciliation.py::run_once_actas_worm`/`_reconciliar_worm_acta`.
Espejo EXACTO de `test_indice_reconciliation_worm.py` (3ª fase, índice).

Pure-unit (pool/conexiones mockeados, `TransferenciaService.proteger_worm_acta`
mockeado) -- mismo estilo que la 3ª fase. La atomicidad REAL de
`proteger_worm_acta` en sí ya está cubierta en `test_transferencia_service_worm.py`
(unit) y `tests/integration/test_transferencia_acta_worm_atomicity.py`
(Postgres real); aquí solo se prueba que el job BARRE, aísla fallos
por-acta, respeta el advisory lock PROPIO (`acta_worm_reconcile`, namespace
distinto de `worm_reconcile`/`indice_reconcile`/`acta_reconcile`) y agrega
el resumen correctamente.
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


def _worm_pending_acta_row(**overrides) -> dict:
    base = {
        "transferencia_id": uuid.uuid4(),
        "minio_key_firmado": f"{uuid.uuid4()}/acta-transferencia.xml",
        "expediente_id": uuid.uuid4(),
        "worm_reconcile_intentos": 0,
        "worm_reconcile_agotado": False,
        "worm_reconcile_ultimo_error": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_reconciliar_worm_acta_delegates_to_transferencia_service() -> None:
    conn = _mock_conn()
    row = _worm_pending_acta_row()

    with patch(
        "app.jobs.indice_reconciliation.TransferenciaService.proteger_worm_acta",
        new=AsyncMock(return_value="protegido"),
    ) as mock_proteger:
        resultado = await indice_reconciliation._reconciliar_worm_acta(conn, "demo", row)

    assert resultado == "protegido"
    mock_proteger.assert_awaited_once_with(
        row["transferencia_id"], actor_id=None, origen="reconciliation"
    )


@pytest.mark.asyncio
async def test_run_once_actas_worm_uses_own_advisory_lock_namespace() -> None:
    """`acta_worm_reconcile` es un namespace de advisory lock PROPIO -- no
    colisiona con `worm_reconcile` (3ª fase, índice) ni con `indice_reconcile`/
    `acta_reconcile` (1ª/2ª fase, sellado)."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True  # pg_try_advisory_lock -> adquirido
    row_a = _worm_pending_acta_row()
    conn_a.fetch.return_value = [row_a]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaService.proteger_worm_acta",
            new=AsyncMock(return_value="protegido"),
        ),
    ):
        summary = await indice_reconciliation.run_once_actas_worm()

    assert summary["pendientes_vistos"] == 1
    assert summary["protegidos"] == 1

    lock_calls = [
        c for c in conn_a.fetchval.call_args_list if c.args and "pg_try_advisory_lock" in c.args[0]
    ]
    assert len(lock_calls) == 1
    assert "acta_worm_reconcile" in lock_calls[0].args[0]

    unlock_calls = [
        c for c in conn_a.execute.call_args_list if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(unlock_calls) == 1
    assert "acta_worm_reconcile" in unlock_calls[0].args[0]


@pytest.mark.asyncio
async def test_run_once_actas_worm_aggregates_across_tenants_and_respects_advisory_lock() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}, {"slug": "tenant-b"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_a = _worm_pending_acta_row()
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
            "app.jobs.indice_reconciliation.TransferenciaService.proteger_worm_acta",
            new=AsyncMock(return_value="protegido"),
        ),
    ):
        summary = await indice_reconciliation.run_once_actas_worm()

    assert summary["pendientes_vistos"] == 1
    assert summary["protegidos"] == 1
    assert summary["fallidos"] == 0
    assert summary["agotados"] == 0
    conn_b.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_run_once_actas_worm_isolates_per_acta_failure() -> None:
    """Un acta que revienta con una excepción inesperada NO aborta la
    barrida del resto del batch de ese tenant."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row_boom = _worm_pending_acta_row()
    row_ok = _worm_pending_acta_row()
    conn_a.fetch.return_value = [row_boom, row_ok]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    async def _side_effect(transferencia_id, *, actor_id, origen):
        if transferencia_id == row_boom["transferencia_id"]:
            raise RuntimeError("boom (itest)")
        return "protegido"

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaService.proteger_worm_acta",
            new=AsyncMock(side_effect=_side_effect),
        ),
    ):
        summary = await indice_reconciliation.run_once_actas_worm()

    assert summary["pendientes_vistos"] == 2
    assert summary["protegidos"] == 1
    assert summary["fallidos"] == 1


@pytest.mark.asyncio
async def test_run_once_actas_worm_counts_agotado_outcome() -> None:
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True
    row = _worm_pending_acta_row(worm_reconcile_intentos=7, worm_reconcile_agotado=False)
    conn_a.fetch.return_value = [row]

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaService.proteger_worm_acta",
            new=AsyncMock(return_value="agotado"),
        ),
    ):
        summary = await indice_reconciliation.run_once_actas_worm()

    assert summary["agotados"] == 1
    assert summary["protegidos"] == 0
    assert summary["fallidos"] == 0


@pytest.mark.asyncio
async def test_run_once_actas_worm_lists_via_transferencia_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El barrido consulta `TransferenciaRepository.list_worm_pending_actas`
    (no `IndexRepository.list_worm_pending`, su gemela del índice) -- fuente
    correcta de la 4ª fase."""
    mock_pool = MagicMock()
    mock_global_conn = AsyncMock()
    mock_global_conn.fetch.return_value = [{"slug": "tenant-a"}]
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_global_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    conn_a = _mock_conn()
    conn_a.fetchval.return_value = True

    called_with: list[int] = []

    async def fake_list_worm_pending_actas(self, limit: int):
        called_with.append(limit)
        return []

    from app.repositories.transferencia_repository import TransferenciaRepository

    monkeypatch.setattr(
        TransferenciaRepository, "list_worm_pending_actas", fake_list_worm_pending_actas
    )

    @asynccontextmanager
    async def fake_acquire_tenant(slug: str):
        yield conn_a

    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=mock_pool),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake_acquire_tenant),
    ):
        summary = await indice_reconciliation.run_once_actas_worm()

    assert summary["pendientes_vistos"] == 0
    assert len(called_with) == 1
