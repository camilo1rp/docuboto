# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.



"""Cableado de los loops de fondo con las fases de la tanda T12 (AREA-07-01/05/10).

Las pruebas de cada fase llaman `run_once_*` directamente; ninguna miraba que
`app/main.py` las INVOQUE. Una fase que nadie programa pasa en verde y no corre
nunca (el backfill del origen, el evento PREMIS de fixity, el WORM de las firmas
personales). Aquí se ejecuta UNA vuelta de cada loop con las fases dobladas."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import main as app_main


class _Fin(Exception):
    pass


async def _una_vuelta(monkeypatch, loop_fn, intervalo_attr: str) -> None:
    monkeypatch.setattr(app_main.settings, intervalo_attr, 0)

    async def _sleep(_s):
        raise _Fin

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(_Fin):
        await loop_fn()


@pytest.mark.asyncio
async def test_el_loop_del_indice_corre_el_backfill_del_origen_legado(monkeypatch) -> None:
    fases = {}
    for nombre in (
        "_reconcile_indices_once", "_reconcile_worm_once", "_reconcile_worm_renovacion_once",
        "_reconcile_aip_once", "_completar_origen_legado_once",
    ):
        fases[nombre] = AsyncMock(return_value={})
        monkeypatch.setattr(app_main, nombre, fases[nombre])
    await _una_vuelta(monkeypatch, app_main._indice_reconciliation_loop,
                      "indice_reconciliation_interval_seconds")
    for nombre, m in fases.items():
        assert m.await_count == 1, nombre


@pytest.mark.asyncio
async def test_el_loop_del_acta_corre_firmas_personales_y_fixity_premis(monkeypatch) -> None:
    fases = {}
    for nombre in (
        "_reconcile_actas_once", "_reconcile_actas_worm_once",
        "_reconcile_actas_eliminacion_worm_once",
        "_reconcile_firmas_personales_worm_once", "_reconcile_fixity_premis_once",
    ):
        fases[nombre] = AsyncMock(return_value={})
        monkeypatch.setattr(app_main, nombre, fases[nombre])
    await _una_vuelta(monkeypatch, app_main._acta_reconciliation_loop,
                      "acta_reconciliation_interval_seconds")
    for nombre, m in fases.items():
        assert m.await_count == 1, nombre


@pytest.mark.asyncio
async def test_una_fase_que_revienta_no_impide_las_siguientes(monkeypatch) -> None:
    fases = {}
    for nombre in (
        "_reconcile_actas_once", "_reconcile_actas_worm_once",
        "_reconcile_actas_eliminacion_worm_once",
        "_reconcile_firmas_personales_worm_once", "_reconcile_fixity_premis_once",
    ):
        fases[nombre] = AsyncMock(return_value={})
        monkeypatch.setattr(app_main, nombre, fases[nombre])
    fases["_reconcile_firmas_personales_worm_once"].side_effect = RuntimeError("boom")
    await _una_vuelta(monkeypatch, app_main._acta_reconciliation_loop,
                      "acta_reconciliation_interval_seconds")
    assert fases["_reconcile_fixity_premis_once"].await_count == 1


@pytest.mark.asyncio
async def test_el_punto_de_entrada_suelto_corre_todas_las_fases(monkeypatch) -> None:
    """`python -m app.jobs.indice_reconciliation` (una pasada manual) no debe dejar fuera
    las fases nuevas: antes omitía el backfill, el evento PREMIS y el WORM de las firmas."""
    from app.jobs import indice_reconciliation as job

    nombres = [n for n in dir(job) if n.startswith("run_once")]
    dobles = {}
    for n in nombres:
        dobles[n] = AsyncMock(return_value={})
        monkeypatch.setattr(job, n, dobles[n])
    monkeypatch.setattr(job, "create_pool", AsyncMock())
    monkeypatch.setattr(job, "close_pool", AsyncMock())
    await job._main()
    for n in ("run_once_origen_legado", "run_once_firmas_personales_worm", "run_once_fixity_premis"):
        assert dobles[n].await_count == 1, n
    assert all(m.await_count == 1 for m in dobles.values()), [
        n for n, m in dobles.items() if m.await_count != 1
    ]
