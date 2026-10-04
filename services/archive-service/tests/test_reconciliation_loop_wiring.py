# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado de los bucles de reconciliación de `app/main.py` (AREA-13-05).

Las piezas (`run_once*`) tienen su cobertura; lo que nadie probaba es el CABLEADO:
que las fases de un bucle estén en `try` independientes (un fallo de la fase 1 no
puede dejar sin correr WORM, renovación, AIP ni el backfill), que cada bucle
espere su intervalo y NUNCA muera por una excepción, y que el `lifespan` cree las
tareas según los kill-switch y las cancele al apagar.

Mutaciones que cazan: sacar `_reconcile_aip_once` de su `try` propio (el fallo de
una fase anterior la deja sin correr), o quitar su llamada del bucle.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app import main

_FASES_INDICE = [
    "_reconcile_indices_once",
    "_reconcile_worm_once",
    "_reconcile_worm_renovacion_once",
    "_reconcile_aip_once",
    "_completar_origen_legado_once",
]
_FASES_ACTA = [
    "_reconcile_actas_once",
    "_reconcile_actas_worm_once",
    "_reconcile_actas_eliminacion_worm_once",
    "_reconcile_firmas_personales_worm_once",
    "_reconcile_fixity_premis_once",
]


class _Corte(Exception):
    """Corta el bucle infinito tras UNA iteración (lo lanza el `sleep` falso)."""


async def _una_iteracion(
    monkeypatch: pytest.MonkeyPatch, bucle: Any, fases: list[str], rota: str | None
) -> dict[str, AsyncMock]:
    mocks = {n: AsyncMock() for n in fases}
    if rota is not None:
        mocks[rota].side_effect = RuntimeError("Postgres inalcanzable")
    for nombre, m in mocks.items():
        monkeypatch.setattr(main, nombre, m)
    esperas: list[float] = []

    async def _sleep(segundos: float) -> None:
        esperas.append(segundos)
        raise _Corte

    monkeypatch.setattr(main.asyncio, "sleep", _sleep)
    with pytest.raises(_Corte):
        await bucle()
    assert len(esperas) == 1, "el bucle debe esperar su intervalo tras cada iteración"
    return mocks


@pytest.mark.parametrize("rota", [None, *_FASES_INDICE])
async def test_el_bucle_del_indice_corre_todas_las_fases_aunque_una_falle(
    monkeypatch: pytest.MonkeyPatch, rota: str | None
) -> None:
    mocks = await _una_iteracion(monkeypatch, main._indice_reconciliation_loop, _FASES_INDICE, rota)
    for nombre, m in mocks.items():
        assert m.await_count == 1, f"la fase {nombre} no corrió (la rota era {rota})"


@pytest.mark.parametrize("rota", [None, *_FASES_ACTA])
async def test_el_bucle_del_acta_corre_todas_las_fases_aunque_una_falle(
    monkeypatch: pytest.MonkeyPatch, rota: str | None
) -> None:
    mocks = await _una_iteracion(monkeypatch, main._acta_reconciliation_loop, _FASES_ACTA, rota)
    for nombre, m in mocks.items():
        assert m.await_count == 1, f"la fase {nombre} no corrió (la rota era {rota})"


async def test_el_bucle_de_disposicion_sobrevive_a_un_fallo(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = await _una_iteracion(
        monkeypatch, main._disposicion_alerta_loop, ["_disposicion_alerta_once"], "_disposicion_alerta_once"
    )
    mocks["_disposicion_alerta_once"].assert_awaited_once()


async def test_cada_bucle_usa_su_propio_intervalo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un bucle no puede heredar la cadencia de otro (el aviso de disposición es
    semanal; bajarlo lo convierte en ruido)."""
    monkeypatch.setattr(main.settings, "indice_reconciliation_interval_seconds", 11)
    monkeypatch.setattr(main.settings, "acta_reconciliation_interval_seconds", 22)
    monkeypatch.setattr(main.settings, "disposicion_alerta_interval_seconds", 33)
    vistos: dict[str, float] = {}
    for clave, bucle, fases in (
        ("indice", main._indice_reconciliation_loop, _FASES_INDICE),
        ("acta", main._acta_reconciliation_loop, _FASES_ACTA),
        ("disposicion", main._disposicion_alerta_loop, ["_disposicion_alerta_once"]),
    ):
        for n in fases:
            monkeypatch.setattr(main, n, AsyncMock())

        async def _sleep(segundos: float, _c: str = clave) -> None:
            vistos[_c] = segundos
            raise _Corte

        monkeypatch.setattr(main.asyncio, "sleep", _sleep)
        with pytest.raises(_Corte):
            await bucle()
    assert vistos == {"indice": 11, "acta": 22, "disposicion": 33}


def _bucles_falsos(monkeypatch: pytest.MonkeyPatch, arrancados: list[str], cancelados: list[str]) -> None:
    def _fabrica(nombre: str) -> Any:
        async def _bucle() -> None:
            arrancados.append(nombre)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelados.append(nombre)
                raise

        return _bucle

    monkeypatch.setattr(main, "_indice_reconciliation_loop", _fabrica("indice"))
    monkeypatch.setattr(main, "_acta_reconciliation_loop", _fabrica("acta"))
    monkeypatch.setattr(main, "_disposicion_alerta_loop", _fabrica("disposicion"))
    monkeypatch.setattr(main, "create_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(main, "close_pool", AsyncMock())


async def test_el_lifespan_crea_las_tres_tareas_y_las_cancela_al_apagar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrancados: list[str] = []
    cancelados: list[str] = []
    _bucles_falsos(monkeypatch, arrancados, cancelados)
    for flag in ("indice_reconciliation_enabled", "acta_reconciliation_enabled", "disposicion_alerta_enabled"):
        monkeypatch.setattr(main.settings, flag, True)

    async with main.lifespan(main.app):
        await asyncio.sleep(0)  # deja arrancar las tareas
        await asyncio.sleep(0)
        assert sorted(arrancados) == ["acta", "disposicion", "indice"]
        assert cancelados == []
    assert sorted(cancelados) == ["acta", "disposicion", "indice"]
    main.close_pool.assert_awaited_once()


@pytest.mark.parametrize(
    "apagado,esperados",
    [
        ("indice_reconciliation_enabled", {"acta", "disposicion"}),
        ("acta_reconciliation_enabled", {"indice", "disposicion"}),
        ("disposicion_alerta_enabled", {"indice", "acta"}),
    ],
)
async def test_el_kill_switch_apaga_solo_su_bucle(
    monkeypatch: pytest.MonkeyPatch, apagado: str, esperados: set[str]
) -> None:
    arrancados: list[str] = []
    cancelados: list[str] = []
    _bucles_falsos(monkeypatch, arrancados, cancelados)
    for flag in ("indice_reconciliation_enabled", "acta_reconciliation_enabled", "disposicion_alerta_enabled"):
        monkeypatch.setattr(main.settings, flag, flag != apagado)

    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert set(arrancados) == esperados
