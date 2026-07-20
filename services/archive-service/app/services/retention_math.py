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
