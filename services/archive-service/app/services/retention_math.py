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

from datetime import UTC, date, datetime, timedelta


def total_trd_years(
    archivo_gestion_years: int | None, archivo_central_years: int | None
) -> int | None:
    """Total de años de retención (archivo de gestión + archivo central) de
    una serie TRD, o ``None`` si el total resuelto queda fuera del rango
    ``[1, 100]`` que exige `RetentionMixin` del lado de storage-service
    (fail-closed: ningún llamador de este helper debe intentar cablear WORM
    con un total fuera de ese rango)."""
    total = (archivo_gestion_years or 0) + (archivo_central_years or 0)
    return total if 1 <= total <= 100 else None


def _restar_anios(d: date, anios: int) -> date:
    """`d` menos `anios` años CALENDARIO (29-feb -> 28-feb), el espejo de la suma
    que hace `storage-service` en `RetentionMixin.resolve_retain_until`."""
    try:
        return d.replace(year=d.year - anios)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year - anios)


def base_worm(
    *,
    origen: str,
    closed_at: datetime | None,
    fecha_extrema_final: date | None,
    total_years: int,
    margen_dias: int,
    hoy: date | None = None,
) -> date | None:
    """`fecha_inicio` con la que se cablea Object-Lock (ADR-026 D4, AREA-06-04).

    storage-service calcula `retain_until = fecha_inicio + total_years`, así que lo que
    hay que decidir aquí es la FECHA BASE, y es lo irreversible (COMPLIANCE):

    - Expediente corriente: la de cierre (`closed_at`), como siempre. `None` si no hay.
    - Fondo acumulado: la `fecha_extrema_final` del fondo, NUNCA `closed_at` (que es la
      del registro y alargaría décadas un lock sobre documentación ya vencida), con
      el CLAMP de la D4.4: `retain_until = max(hoy + margen, fecha_extrema_final +
      total)`. El calendario de un fondo suele estar vencido (es el caso normal), y
      storage rechaza un `retain_until` anterior a su piso de seguridad; cuando el
      resultado natural cae por debajo, se devuelve la base que deja `retain_until`
      en `hoy + margen`. Sin `fecha_extrema_final` (el CHECK de la base lo impide, pero
      no se asume): `None` y el llamador pospone, fail-closed.

    `margen_dias` debe superar el piso de storage (`PRESERVATION_MIN_RETENTION_DAYS`,
    365 por defecto) para que un día de más por bisiesto no lo deje por debajo.
    """
    if origen != "fondo_acumulado":
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
    return _restar_anios(minimo, total_years)
