# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Validación PDF/A del AIP en ingesta (E10 Increment C2a/C2b)."""

from storage.services.pdfa.factory import get_pdfa_validator
from storage.services.pdfa.runner import run_pdfa_validation
from storage.services.pdfa.stub import StubPdfaValidator
from storage.services.pdfa.subprocess_verapdf import SubprocessVeraPdfValidator
from storage.services.pdfa.types import PDFA_RESULTADO_MAP, PdfaOutcome, PdfaResult, PdfaValidator

__all__ = [
    "PDFA_RESULTADO_MAP",
    "PdfaOutcome",
    "PdfaResult",
    "PdfaValidator",
    "StubPdfaValidator",
    "SubprocessVeraPdfValidator",
    "get_pdfa_validator",
    "run_pdfa_validation",
]
