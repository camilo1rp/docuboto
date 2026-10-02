# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Estado del radicado derivado del flujo (ADR-028).

Una sola función pura: la decide el workflow-service en la MISMA transacción que
el paso, y el backfill SQL (`migrations/tenant/013_radicado_estado_desde_flujo.sql`)
es su gemelo literal. Si se toca una, se toca la otra: la prueba de integración
compara cada fila del backfill con esta función.

Estados: `registered` -> `distributed` -> `in_progress` -> `closed`; `anulado` lo
escribe la anulación (document-service) y, junto con `closed`, es terminal.
"""

from __future__ import annotations

ESTADOS_TERMINALES: frozenset[str] = frozenset({"closed", "anulado"})


def derivar_estado_radicado(
    actual: str,
    paso_activo: str | None,
    *,
    tiene_pasos: bool,
    finalizar: bool = False,
) -> str:
    """Estado que le corresponde al radicado dado su paso activo.

    - `closed`/`anulado` son terminales: nada los mueve (ni siquiera `finalizar`).
    - `finalizar` (acto explícito «Finalizar trámite») -> `closed`.
    - Paso activo `pending` -> `distributed`; `in_progress` -> `in_progress`
      (solo lo produce `marcar_leido` del poseedor).
    - Sin paso activo: un `registered` que ya tuvo pasos pasa a `distributed`;
      cualquier otro estado se conserva. Completar un paso sin marcarlo leído
      deja `distributed`: es una decisión declarada, no un olvido.
    """
    if actual in ESTADOS_TERMINALES:
        return actual
    if finalizar:
        return "closed"
    if paso_activo == "pending":
        return "distributed"
    if paso_activo == "in_progress":
        return "in_progress"
    return "distributed" if (actual == "registered" and tiene_pasos) else actual
