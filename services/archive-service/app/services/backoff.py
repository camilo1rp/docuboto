# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Backoff exponencial compartido por las fases de reconciliación (residual
Ac. AGN 001/2024 art. 4.3.2.4: sellado del índice/acta, y E10 Inc.1: cableado
WORM del índice).

Extraído de `app/jobs/indice_reconciliation.py::_next_backoff_seconds` (antes
un helper module-level solo de ese archivo, reusado ahí mismo por las fases
del índice Y del acta) para que `IndexService.proteger_worm_indice` (que NO
es un job, corre tanto en el disparo síncrono del cierre como dentro de
`run_once_worm`) pueda usar la MISMA fórmula sin que `app/services/` importe
de `app/jobs/` (capas invertidas)."""

from __future__ import annotations


def next_backoff_seconds(intentos_tras_este_fallo: int, *, base: int, cap: int) -> int:
    """`min(cap, base * 2**intentos)`. El exponente se acota (no solo el
    resultado) para no desbordar un int nativo con un contador de intentos
    anómalamente alto."""
    return min(cap, base * (2 ** min(intentos_tras_este_fallo, 20)))
