# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Interfaz de validación PDF/A (E10 Increment C2a, RF-PRE-02, ISO 19005).

Contrato mínimo para que un validador de conformidad PDF/A se enchufe en el
empaquetado del AIP (`app.services.aip_service.AipService.empaquetar_aip`).
La implementación REAL con veraPDF vía subprocess es un incremento
POSTERIOR (E10 Increment C2b, roadmap) — este módulo solo fija la interfaz;
`app.services.pdfa.stub.StubPdfaValidator` es el único validador que existe
hoy, y es deliberadamente HONESTO (RF-FIR-15): jamás finge haber evaluado
conformidad cuando no lo hizo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# "not_evaluated" NUNCA implica conformidad ni no-conformidad — es un tercer
# estado honesto, distinto de "conformant"/"non_conformant" (migración 008
# habilita su espejo en `preservacion_evento.resultado` como 'no_evaluado').
PdfaOutcome = Literal["conformant", "non_conformant", "not_evaluated"]

# Espejo de `PdfaOutcome` al vocabulario de `preservacion_evento.resultado`
# (migración 008: 'ok'|'fallo'|'no_evaluado'). Compartido entre
# `app.services.aip_service` (empaquetado del AIP) y
# `app.services.pdfa_ingest_service` (E10 Increment C2b follow-up, validación
# en ingesta E07) — AMBOS insertan eventos `tipo=VALIDACION_PDFA` con el
# mismo mapeo, para que el rastro PREMIS use siempre el mismo vocabulario sin
# importar en qué punto del ciclo de vida se validó.
PDFA_RESULTADO_MAP: dict[str, str] = {
    "conformant": "ok",
    "non_conformant": "fallo",
    "not_evaluated": "no_evaluado",
}


@dataclass(slots=True)
class PdfaResult:
    """Resultado de intentar validar un documento contra un perfil PDF/A.

    `validator_agent` identifica al validador que produjo el resultado (p.
    ej. ``"stub/validator_unavailable"`` o, en el futuro Increment C2b,
    ``"veraPDF x.y (profile 2b)"``) — se usa tanto en `preservacion_evento.agente`
    como en el PREMIS `agent`/`linkingAgentIdentifier` del evento `validation`.
    """

    outcome: PdfaOutcome
    profile: str
    validator_agent: str
    detail: dict[str, Any] = field(default_factory=dict)


class PdfaValidator(Protocol):
    """Validador PDF/A best-effort: NUNCA lanza, siempre devuelve un `PdfaResult`.

    Recibe `pdf_path` (una ruta a archivo, no los bytes en memoria) porque la
    implementación real (E10 Increment C2b) invoca el binario `veraPDF` vía
    subprocess, que opera sobre un archivo en disco — el llamador
    (`app.services.pdfa.runner.run_pdfa_validation`) materializa en un
    archivo temporal los bytes YA descargados/verificados (fixity
    recalculada por `AipService._resolve_and_verify`) ANTES de invocar esto,
    y lo limpia después, sin importar cuál implementación concreta corra.
    """

    async def validate(self, pdf_path: str, *, profile: str) -> PdfaResult: ...
