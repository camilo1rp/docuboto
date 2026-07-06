# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Orquestación best-effort de la validación PDF/A (E10 Increment C2a).

`run_pdfa_validation` materializa en un archivo temporal los bytes YA
descargados/verificados (fixity recalculada por
`AipService._resolve_and_verify` ANTES de llegar aquí), invoca el validador
activo y SIEMPRE limpia el archivo — NUNCA propaga una excepción: cualquier
fallo (I/O, un futuro timeout del subproceso veraPDF en Increment C2b, un
bug del validador) degrada a `outcome="not_evaluated"`, nunca tumba el
empaquetado del AIP (PIEZA 5 del contrato, advisory por defecto).

CRÍTICO — se invoca ANTES de abrir `conn.transaction()` en
`AipService.empaquetar_aip`: un subprocess (futuro Increment C2b) no puede
correr con la transacción de BD abierta (retendría la conexión/locks), y un
fallo/timeout de la validación NUNCA debe poder hacer rollback del AIP.
"""

from __future__ import annotations

import logging
import os
import tempfile

from app.services.pdfa.types import PdfaResult, PdfaValidator

logger = logging.getLogger(__name__)


async def run_pdfa_validation(
    validator: PdfaValidator, content: bytes, *, profile: str
) -> PdfaResult:
    """Ejecuta `validator.validate()` sobre `content` sin nunca lanzar.

    El Protocol `PdfaValidator.validate()` ya se declara "nunca lanza", pero
    esta función además envuelve la plomería de disco (creación/limpieza del
    archivo temporal) — un fallo ahí (p. ej. disco lleno) tampoco debe poder
    bloquear el AIP.
    """
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        return await validator.validate(path, profile=profile)
    except Exception as exc:  # noqa: BLE001 — best-effort: nunca bloquea el AIP
        logger.warning("Validación PDF/A best-effort fallida: %s", exc)
        return PdfaResult(
            outcome="not_evaluated",
            profile=profile,
            validator_agent="runner/error",
            detail={"motivo": f"excepción durante la validación best-effort: {exc}"},
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
