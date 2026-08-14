# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
El conjunto `NOMBRES_AMBIGUOS` no se mantiene a mano: se revalida contra el
repositorio.

Una lista escrita a mano que nadie recalcula caduca en silencio — y su caducidad
tiene exactamente la forma del defecto que evita: una migración nueva que colisione
con la de otro servicio no entraría en el conjunto, el runner se la auto-atribuiría
y volvería a marcar como aplicada una que nunca corrió.

Si este test falla al añadir una migración, hay dos salidas legítimas: renombrarla
para que no colisione (preferible) o añadirla al conjunto.
"""

from __future__ import annotations

import os
from collections import defaultdict

import pytest

from orpycamcp_common.migrations import NOMBRES_AMBIGUOS


def _raiz_repo() -> str | None:
    aqui = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(aqui, "services")):
            return aqui
        aqui = os.path.dirname(aqui)
    return None


def _colisiones(raiz: str) -> set[str]:
    por_nombre: dict[tuple[str, str], set[str]] = defaultdict(set)
    servicios = os.path.join(raiz, "services")
    for svc in sorted(os.listdir(servicios)):
        for ambito in ("global", "tenant"):
            d = os.path.join(servicios, svc, "migrations", ambito)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if f.endswith(".sql"):
                    por_nombre[(ambito, f)].add(svc)
    return {f for (_, f), svcs in por_nombre.items() if len(svcs) > 1}


def test_el_conjunto_de_nombres_ambiguos_coincide_con_el_repositorio() -> None:
    raiz = _raiz_repo()
    if raiz is None:
        pytest.skip("los directorios de servicios no están montados en esta imagen")

    reales = _colisiones(raiz)
    faltan = reales - set(NOMBRES_AMBIGUOS)
    sobran = set(NOMBRES_AMBIGUOS) - reales

    assert not faltan, (
        "hay migraciones cuyo nombre declaran dos servicios y no están en "
        f"NOMBRES_AMBIGUOS: {sorted(faltan)}. Un runner se las auto-atribuiría y "
        "marcaría como aplicada una migración que nunca corrió."
    )
    assert not sobran, (
        f"NOMBRES_AMBIGUOS lista nombres que ya no colisionan: {sorted(sobran)}. "
        "Sobrar no rompe nada (solo hace que se reapliquen migraciones "
        "idempotentes), pero la lista deja de describir el repositorio."
    )
