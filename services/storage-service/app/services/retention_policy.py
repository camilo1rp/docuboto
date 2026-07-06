# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación de retención WORM compartida (E10 Increment A + Increment B).

Extraído de ``preservation_worm_service.py`` (Increment A) para que
``aip_service.py`` (Increment B) no duplique el piso/techo de seguridad: ambos
flujos protegen artefactos bajo el mismo bucket WORM y deben respetar los
mismos límites de cordura, aunque el llamador (archive-service) aporte
``retain_until``/``retencion_anios`` de forma independiente en cada endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import settings


class InvalidRetentionError(Exception):
    """``retain_until`` ausente, no futuro, o fuera de los límites configurados."""


def validate_retain_until(retain_until: datetime) -> None:
    now = datetime.now(tz=UTC)
    if retain_until <= now:
        raise InvalidRetentionError("retain_until debe ser una fecha futura")
    max_dt = now + timedelta(days=365 * settings.preservation_max_retention_years)
    if retain_until > max_dt:
        raise InvalidRetentionError(
            f"retain_until excede el máximo configurado "
            f"({settings.preservation_max_retention_years} años)"
        )
    min_dt = now + timedelta(days=settings.preservation_min_retention_days)
    if retain_until < min_dt:
        raise InvalidRetentionError(
            "retain_until implica menos de "
            f"{settings.preservation_min_retention_days} días de retención "
            "(piso de seguridad WORM)"
        )
