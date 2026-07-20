# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Factory del validador PDF/A activo (E10 Increment C2a/C2b).

Degradación HONESTA: si se pide `PDFA_VALIDATOR=verapdf` pero
`VERAPDF_BINARY` no apunta a un ejecutable real (ruta absoluta existente),
cae a `StubPdfaValidator` con un warning en el log — NUNCA levanta una
excepción de arranque por esto. El gate que decide si se valida algo en
absoluto es `PRESERVATION_PDFA_VALIDATION_ENABLED` (`app.core.config.Settings`),
no la elección de validador.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

from app.services.pdfa.stub import StubPdfaValidator
from app.services.pdfa.subprocess_verapdf import SubprocessVeraPdfValidator
from app.services.pdfa.types import PdfaValidator

logger = logging.getLogger(__name__)


class _SettingsLike(Protocol):
    pdfa_validator: str


def _resolve_verapdf_binary(settings: _SettingsLike) -> str | None:
    """Valida `settings.verapdf_binary`: debe ser una ruta ABSOLUTA a un
    archivo existente y ejecutable. `getattr` con default porque algunos
    `settings`-like usados en tests (y despliegues legacy sin este
    incremento) no declaran el atributo -- eso también debe degradar a
    stub, no lanzar `AttributeError`."""
    binary = getattr(settings, "verapdf_binary", None)
    if not binary:
        return None
    if not os.path.isabs(binary):
        logger.warning("VERAPDF_BINARY=%r no es una ruta absoluta -- ignorado.", binary)
        return None
    if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
        logger.warning("VERAPDF_BINARY=%r no existe o no es ejecutable -- ignorado.", binary)
        return None
    return binary


def get_pdfa_validator(settings: _SettingsLike) -> PdfaValidator:
    """Resuelve el validador PDF/A a usar según `settings.pdfa_validator`.

    "verapdf" con un `VERAPDF_BINARY` válido devuelve
    `SubprocessVeraPdfValidator` (E10 Increment C2b) real. Sin un binario
    válido (dev slim sin Java, o despliegue mal configurado), degrada a
    `StubPdfaValidator` (honesto: `outcome=not_evaluated`) con un warning
    explícito, en vez de fallar el arranque o el empaquetado del AIP.
    """
    validator_name = settings.pdfa_validator

    if validator_name == "verapdf":
        binary = _resolve_verapdf_binary(settings)
        if binary is not None:
            return SubprocessVeraPdfValidator(
                binary=binary,
                timeout_seconds=getattr(settings, "verapdf_timeout_seconds", 60),
                max_concurrency=getattr(settings, "verapdf_max_concurrency", 1),
                jvm_max_heap_mb=getattr(settings, "verapdf_jvm_max_heap_mb", 512),
                jvm_max_metaspace_mb=getattr(settings, "verapdf_jvm_max_metaspace_mb", 256),
            )
        logger.warning(
            "PDFA_VALIDATOR=verapdf solicitado, pero VERAPDF_BINARY no apunta a un "
            "ejecutable real en este despliegue -- degradando a StubPdfaValidator "
            "(outcome=not_evaluated, no finge conformidad)."
        )
        return StubPdfaValidator()

    if validator_name != "stub":
        logger.warning(
            "PDFA_VALIDATOR=%r desconocido -- usando StubPdfaValidator", validator_name
        )

    return StubPdfaValidator()
