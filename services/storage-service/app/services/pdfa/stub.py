# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
`StubPdfaValidator`: el validador PDF/A por DEFECTO (E10 Increment C2a).

HONESTIDAD (RF-FIR-15): nunca finge validar. Devuelve SIEMPRE
`outcome="not_evaluated"` — "no evaluado" es semánticamente distinto de
"passed"/"conformant": NO afirma que el documento sea PDF/A conforme, ni que
no lo sea. veraPDF real (E10 Increment C2b, roadmap) es una implementación
futura del mismo `PdfaValidator` Protocol que reemplazaría este stub cuando
`PDFA_VALIDATOR=verapdf` Y el binario esté disponible en el despliegue.
"""

from __future__ import annotations

from app.services.pdfa.types import PdfaResult

# Identificador estable del "agente" que produjo el resultado — aparece tal
# cual en `preservacion_evento.agente` y en el PREMIS (agent/linkingAgentIdentifier)
# del evento `validation`, para que quede explícito en ambos lugares que la
# validación PDF/A real NO corrió.
VALIDATOR_AGENT = "stub/validator_unavailable"


class StubPdfaValidator:
    """Validador PDF/A por defecto: NO evalúa conformidad, lo declara explícitamente."""

    async def validate(self, pdf_path: str, *, profile: str) -> PdfaResult:
        return PdfaResult(
            outcome="not_evaluated",
            profile=profile,
            validator_agent=VALIDATOR_AGENT,
            detail={
                "motivo": (
                    "Validador PDF/A real no disponible en este despliegue "
                    "(veraPDF es E10 Increment C2b, roadmap) — la "
                    "conformidad PDF/A NO fue evaluada, ni positiva ni "
                    "negativamente."
                ),
            },
        )
