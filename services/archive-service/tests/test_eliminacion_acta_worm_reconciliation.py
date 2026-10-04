# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reconciliación del WORM del ACTA DE ELIMINACIÓN (AREA-06-02).

El acta tenía su candado de un solo intento: un fallo del almacén dejaba la fila
sin `worm_reconcile_proximo_at` y el índice parcial
`ix_eliminacion_acta_firma_worm_pendiente` (migración 050) sin ningún
consumidor — `grep eliminacion app/jobs/` salía vacío mientras el índice y el
acta de transferencia sí tenían el suyo. Y como `ejecutar_lote_eliminacion`
ahora exige el candado antes de destruir, una acta sin candado y sin job
bloquea la destrucción para siempre.

Lo que se prueba, con el servicio REAL detrás del job (solo el almacén y la
base están doblados): que la barrida alcanza el acta firmada sin candado, que
llama al almacén con `objeto_tipo='lote'`, que el listado excluye lo que no
debe tocar y que un fallo se aísla por acta.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.jobs import indice_reconciliation
from tests.test_eliminacion_acta import LOTE, _acta_row, _WormFallo, _WormOk


def _conn(filas_pendientes: list[dict], acta: dict | None) -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    conn.fetch.return_value = filas_pendientes
    conn.fetchval.return_value = True  # el advisory lock se adquiere
    conn.fetchrow.return_value = acta
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _pool(slugs: list[str]) -> MagicMock:
    pool = MagicMock()
    global_conn = AsyncMock()
    global_conn.fetch.return_value = [{"slug": s} for s in slugs]
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=global_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _barrer(conn: AsyncMock, *, worm):
    @asynccontextmanager
    async def _adquirir(slug: str):
        yield conn

    return (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=_pool(["demo"])),
        patch("app.jobs.indice_reconciliation.acquire_tenant", _adquirir),
        patch("app.clients.storage_client.proteger_artefacto_worm", AsyncMock(return_value=worm)),
    )


def _fila_pendiente(**extra) -> dict:
    return {
        "lote_id": LOTE,
        "minio_key_firmado": "k",
        "worm_reconcile_intentos": 0,
        **extra,
    }


@pytest.mark.asyncio
async def test_la_barrida_protege_el_acta_firmada_sin_candado() -> None:
    conn = _conn([_fila_pendiente()], _acta_row(firma_estado="firmado"))
    p_pool, p_acquire, p_worm = _barrer(conn, worm=_WormOk())

    with p_pool, p_acquire, p_worm as proteger:
        resumen = await indice_reconciliation.run_once_actas_eliminacion_worm()

    assert resumen["pendientes_vistos"] == 1
    assert resumen["protegidos"] == 1
    kwargs = proteger.await_args.kwargs
    assert kwargs["objeto_tipo"] == "lote"
    assert kwargs["expediente_id"] == str(LOTE)
    assert kwargs["tipo"] == "acta_eliminacion"
    # Atribuida al principal de sistema, no a una persona.
    from app.core.constants import SYSTEM_RECONCILER_ID

    assert kwargs["actor_id"] == str(SYSTEM_RECONCILER_ID)
    # Y con su propio namespace de advisory lock.
    lock = next(
        c for c in conn.fetchval.call_args_list if c.args and "pg_try_advisory_lock" in c.args[0]
    )
    assert "acta_eliminacion_worm_reconcile" in lock.args[0]


@pytest.mark.asyncio
async def test_el_listado_excluye_lo_que_no_hay_que_proteger() -> None:
    """El SQL EMITIDO: solo actas 'firmado', sin proteger, no agotadas y con el
    backoff cumplido. Sin la condición del backoff, la barrida martillearía el
    almacén caído en cada ciclo."""
    conn = _conn([], None)
    p_pool, p_acquire, p_worm = _barrer(conn, worm=_WormOk())

    with p_pool, p_acquire, p_worm:
        resumen = await indice_reconciliation.run_once_actas_eliminacion_worm()

    assert resumen["pendientes_vistos"] == 0
    sql = conn.fetch.await_args.args[0]
    assert "FROM eliminacion_acta_firma" in sql
    assert "estado = 'firmado'" in sql
    assert "worm_protegido_at IS NULL" in sql
    assert "worm_reconcile_agotado = FALSE" in sql
    assert "worm_reconcile_proximo_at <= now()" in sql


@pytest.mark.asyncio
async def test_un_fallo_del_almacen_programa_el_reintento_y_no_aborta_la_barrida() -> None:
    otra = uuid.uuid4()
    conn = _conn(
        [_fila_pendiente(), _fila_pendiente(lote_id=otra)],
        _acta_row(firma_estado="firmado"),
    )
    p_pool, p_acquire, p_worm = _barrer(conn, worm=_WormFallo())

    with p_pool, p_acquire, p_worm as proteger:
        resumen = await indice_reconciliation.run_once_actas_eliminacion_worm()

    assert resumen["pendientes_vistos"] == 2
    assert resumen["fallidos"] == 2
    assert proteger.await_count == 2
    # Cada fallo dejó su reintento programado (no un NULL sin dueño).
    programados = [
        c.args
        for c in conn.execute.call_args_list
        if c.args and isinstance(c.args[0], str) and "worm_reconcile_proximo_at = $3" in c.args[0]
    ]
    assert len(programados) == 2
    assert all(a[3] is not None and a[4] is False for a in programados)
