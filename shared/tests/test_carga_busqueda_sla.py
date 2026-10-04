# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-09-17: la aritmética y el veredicto del guion de carga de la búsqueda.

El guion (`scripts/carga/busqueda_sla.py`) mide contra la pila viva y no corre en el
gate; lo que SÍ se puede vigilar sin pila es que su cuenta sea honesta: un SLA se
verifica con el percentil 95, y el estimador no puede MEJORAR la cifra observada; y un
escenario que no pudo medirse (cero respuestas correctas) nunca cuenta como cumplido.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "carga" / "busqueda_sla.py"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="scripts/ no está montado")


def _mod() -> Any:
    spec = importlib.util.spec_from_file_location("busqueda_sla_under_test", _SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # los dataclass con anotaciones diferidas lo necesitan
    spec.loader.exec_module(m)
    return m


def test_el_p95_de_veinte_muestras_es_la_diecinueve() -> None:
    m = _mod()
    muestras = [float(i) for i in range(1, 21)]  # 1..20
    assert m.percentil(muestras, 95) == 19.0
    assert m.percentil(muestras, 50) == 10.0
    # Un único valor lento DEBE verse en el p100, no quedar diluido.
    assert m.percentil(muestras + [999.0], 100) == 999.0


def test_el_percentil_no_mejora_el_peor_caso_con_pocas_muestras() -> None:
    m = _mod()
    # Con 3 muestras el p95 es la mayor: interpolar daría un valor por debajo de lo observado.
    assert m.percentil([0.1, 0.2, 5.0], 95) == 5.0


def test_sin_muestras_no_hay_percentil() -> None:
    with pytest.raises(ValueError):
        _mod().percentil([], 95)


def _res(m: Any, *, muestras: int = 10, errores: int = 0, p95: float = 0.1, umbral: float = 3.0) -> Any:
    return m.Resultado("simple", "u", muestras, errores, 0.05, p95, umbral)


def test_el_veredicto_distingue_cumple_incumple_y_no_se_pudo_medir() -> None:
    m = _mod()
    assert m.veredicto([_res(m)]) == 0
    assert m.veredicto([_res(m), _res(m, p95=3.5)]) == 1  # un p95 sobre el umbral
    assert m.veredicto([_res(m, p95=3.0)]) == 1  # el umbral es «menos de»: igual no cumple
    assert m.veredicto([_res(m, errores=1)]) == 1  # un error rompe el escenario
    assert m.veredicto([]) == 2
    assert m.veredicto([_res(m, muestras=0, p95=float("nan"))]) == 2


def test_los_umbrales_son_los_de_rf_bus_09() -> None:
    m = _mod()
    assert (m.UMBRAL_SIMPLE_S, m.UMBRAL_COMPLEJA_S) == (3.0, 10.0)
    assert set(m.ESCENARIOS) == {"simple", "compleja"}
    # La búsqueda compleja lleva cuatro términos de texto, como pide el requisito.
    assert len(m.ESCENARIOS["compleja"][1]["q"].split()) == 4
