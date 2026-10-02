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

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

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


# ---------------------------------------------------------------------------
# Ubicación actual del radicado (ADR-029)
# ---------------------------------------------------------------------------

ESTADOS_PASO_ACTIVO: frozenset[str] = frozenset({"pending", "in_progress"})


@dataclass(frozen=True, slots=True)
class PasoUbicacion:
    """Lo que `derivar_ubicacion` necesita de un `flow_steps`.

    `codigos_candidatos` son los `dependencias.codigo` ACTIVOS cuyo nombre es
    exactamente `to_dept` (el paso guarda el NOMBRE, sin FK: `dependencias.nombre`
    no es único). Lo resuelve la consulta; la decisión de qué hacer con ellos es
    de la función.
    """

    step_number: int
    status: str
    to_dept: str
    assigned_to: uuid.UUID | None
    completed_at: datetime | None
    codigos_candidatos: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Ubicacion:
    """`radicados.flujo_dept_code` / `flujo_dept` / `flujo_usuario`."""

    dept: str
    dept_code: int | None
    usuario: uuid.UUID | None


def resolver_codigo_dependencia(candidatos: Sequence[int]) -> int | None:
    """El código solo se resuelve si el nombre casa con UNA dependencia activa.

    Con cero (la dependencia se desactivó o el paso lleva una errata) o con varias
    (el nombre no es único) elegir una sería inventar la ubicación: se devuelve
    `None` y el nombre queda como instantánea en `flujo_dept`.
    """
    return candidatos[0] if len(candidatos) == 1 else None


def _paso_de_ubicacion(pasos: Sequence[PasoUbicacion]) -> PasoUbicacion | None:
    activos = [p for p in pasos if p.status in ESTADOS_PASO_ACTIVO]
    if activos:
        return max(activos, key=lambda p: p.step_number)
    if not pasos:
        return None
    # «Último» por CUÁNDO se cerró, no por número de paso: tras un rollback el paso
    # cancelado tiene el número más alto pero el anterior —reactivado y completado
    # después— es donde el radicado estuvo por última vez. Ordenar por
    # `step_number` pondría el radicado en una dependencia que lo canceló.
    return max(
        pasos,
        key=lambda p: (
            p.completed_at is not None,
            p.completed_at or datetime.min.replace(tzinfo=UTC),
            p.step_number,
        ),
    )


def derivar_ubicacion(
    actual: str,
    pasos: Sequence[PasoUbicacion],
    *,
    congelada: Ubicacion | None = None,
) -> Ubicacion | None:
    """Dependencia (y poseedor) donde está el radicado, según sus pasos.

    - Terminal (`closed`/`anulado`) con ubicación ya escrita: se CONSERVA. Nada la
      mueve después (la anulación no la reescribe): un cerrado queda en la
      dependencia donde se finalizó y un anulado donde estaba al anularse.
    - Con paso activo: el de mayor `step_number` (`pending` o `in_progress`).
    - Sin paso activo: el paso que se cerró más recientemente (`completed_at`
      DESC, desempate `step_number` DESC), en CUALQUIER estado.
    - Sin pasos: `None` — la lectura cae a la dependencia de radicación.

    Gemela de `migrations/tenant/016_radicado_ubicacion_desde_flujo.sql`; la
    prueba de integración compara cada fila del backfill con esta función.
    """
    if actual in ESTADOS_TERMINALES and congelada is not None:
        return congelada
    paso = _paso_de_ubicacion(pasos)
    if paso is None:
        return None
    return Ubicacion(
        dept=paso.to_dept,
        dept_code=resolver_codigo_dependencia(paso.codigos_candidatos),
        usuario=paso.assigned_to,
    )
