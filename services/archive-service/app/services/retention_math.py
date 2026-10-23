# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Aritmética de retención TRD compartida por el cableado WORM del índice
(`IndexService._resolve_worm_retention`/`_resolve_ct_retencion_anios`, E10
Inc.1 + debt) y del acta (`TransferenciaService._resolve_worm_retention_acta`/
`_resolve_ct_retencion_anios_acta`, E10 Inc.2 wiring + debt).

Extraído para que la MISMA regla de cordura (suma de las dos fases de la TRD,
acotada a ``[1, 100]`` años -- defensa en profundidad, espejo del piso/techo
que `storage-service.app.services.retention_policy.validate_retain_until` ya
aplica del otro lado del cableado) no diverja entre los cuatro sitios que la
necesitan (protección índice/acta, renovación índice/acta)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal


def total_trd_years(
    archivo_gestion_years: int | None, archivo_central_years: int | None
) -> int | None:
    """Total de años de retención (archivo de gestión + archivo central) de
    una serie TRD, o ``None`` si el total resuelto queda fuera del rango
    ``[1, 100]`` que exige `RetentionMixin` del lado de storage-service
    (fail-closed: ningún llamador de este helper debe intentar cablear WORM
    con un total fuera de ese rango)."""
    if archivo_central_years is None:
        # C-4/C6: la fase central es obligatoria (CHECK de la 069). Faltante = dato
        # corrupto: ``None`` (el llamador pospone, fail-closed) y no un 0 silencioso
        # que se leería como «sin retención». La de gestión SÍ puede faltar: la TVD
        # no tiene esa fase.
        return None
    total = (archivo_gestion_years or 0) + archivo_central_years
    return total if 1 <= total <= 100 else None


@dataclass(frozen=True, slots=True)
class RetencionDiferida:
    """Resultado de `base_worm` para el CONTENIDO de un fondo acumulado cuyo calendario ya
    venció (ADR-043, enmienda de ADR-026 D4.4): NO hay `retain_until` que fijar. El objeto se
    protege solo con el legal hold `valoracion_pendiente`, reversible, hasta que el Comité
    valore la TVD. Un COMPLIANCE sobre contenido ya vencido sería irreversible y no tendría
    salida; el hold sí."""

    motivo: Literal["valoracion_pendiente"] = "valoracion_pendiente"


def base_worm(
    *,
    origen: str,
    closed_at: datetime | None,
    fecha_extrema_final: date | None,
    total_years: int,
    margen_dias: int,
    hoy: date | None = None,
    tipo: Literal["contenido", "instrumento"] = "contenido",
) -> date | RetencionDiferida | None:
    """`fecha_inicio` con la que se cablea Object-Lock (ADR-026 D4, AREA-06-04; ADR-043).

    storage-service calcula `retain_until = fecha_inicio + total_years`, así que lo que
    hay que decidir aquí es la FECHA BASE (lo irreversible, COMPLIANCE) o, para el
    contenido de un fondo vencido, que NO se fija retención:

    - `tipo="instrumento"` (acta de transferencia y derivadas): la base es SIEMPRE el
      acto (`closed_at` = `firmado_at` del acta), también en un fondo acumulado. No
      posponen la disposición del contenido y deben sobrevivir a él como prueba; con
      la regla del contenido una valoración «E» los dejaría sin protección.
    - Expediente corriente: la de cierre (`closed_at`). `None` si no hay.
    - Fondo acumulado (contenido: índice y AIP): la `fecha_extrema_final` del fondo,
      NUNCA `closed_at`. Si `fecha_extrema_final + total >= hoy + margen` el
      calendario sigue vigente: COMPLIANCE natural con base `fecha_extrema_final`.
      Si ya venció (el caso normal de un fondo) devuelve `RetencionDiferida`: hold
      `valoracion_pendiente`, sin retención. Nunca se fija un `retain_until` mayor que
      el natural. Sin `fecha_extrema_final` (el CHECK de la base lo impide, pero no
      se asume): `None` y el llamador pospone, fail-closed.

    `margen_dias` (config `worm_piso_margen_dias`) debe superar el piso de storage
    (`PRESERVATION_MIN_RETENTION_DAYS`, 365 por defecto) para que un día de más por
    bisiesto no deje una retención natural justo por debajo de él.
    """
    if tipo == "instrumento" or origen != "fondo_acumulado":
        return closed_at.date() if closed_at is not None else None
    if fecha_extrema_final is None:
        return None
    hoy = hoy or datetime.now(tz=UTC).date()
    if fecha_extrema_final > hoy:
        # Defensa en profundidad del validador de schemas: un fondo con base futura (dato
        # legado o ruta sin validar) alargaría un COMPLIANCE irreversible. Fail-closed: el
        # llamador pospone con backoff y alguien corrige la fecha.
        return None
    minimo = hoy + timedelta(days=margen_dias)
    try:
        natural = fecha_extrema_final.replace(year=fecha_extrema_final.year + total_years)
    except ValueError:
        natural = fecha_extrema_final.replace(
            month=2, day=28, year=fecha_extrema_final.year + total_years
        )
    if natural >= minimo:
        return fecha_extrema_final
    return RetencionDiferida()
