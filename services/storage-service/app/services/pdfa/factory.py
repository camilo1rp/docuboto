# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Factory del validador PDF/A activo (E10 Increment C2a).

Degradación HONESTA: si se pide `PDFA_VALIDATOR=verapdf` pero la
implementación real (`SubprocessVeraPdfValidator`, E10 Increment C2b) aún no
existe en este despliegue, cae a `StubPdfaValidator` con un warning en el
log — NUNCA levanta una excepción de arranque por esto. El gate que decide
si se valida algo en absoluto es `PRESERVATION_PDFA_VALIDATION_ENABLED`
(`app.core.config.Settings`), no la elección de validador.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.services.pdfa.stub import StubPdfaValidator
from app.services.pdfa.types import PdfaValidator

logger = logging.getLogger(__name__)


class _SettingsLike(Protocol):
    pdfa_validator: str


def get_pdfa_validator(settings: _SettingsLike) -> PdfaValidator:
    """Resuelve el validador PDF/A a usar según `settings.pdfa_validator`.

    "verapdf" está RESERVADO para E10 Increment C2b — hoy no hay ninguna
    implementación real que invocar, así que degrada a `StubPdfaValidator`
    (honesto: `outcome=not_evaluated`) con un warning explícito, en vez de
    fallar el arranque o el empaquetado del AIP.
    """
    validator_name = settings.pdfa_validator

    if validator_name == "verapdf":
        logger.warning(
            "PDFA_VALIDATOR=verapdf solicitado, pero el validador real "
            "(veraPDF vía subprocess, E10 Increment C2b) aún no está "
            "implementado en este despliegue -- degradando a "
            "StubPdfaValidator (outcome=not_evaluated, no finge conformidad)."
        )
        return StubPdfaValidator()

    if validator_name != "stub":
        logger.warning(
            "PDFA_VALIDATOR=%r desconocido -- usando StubPdfaValidator", validator_name
        )

    return StubPdfaValidator()
