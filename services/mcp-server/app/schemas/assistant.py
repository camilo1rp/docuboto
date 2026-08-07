# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Schemas Pydantic v2 del asistente conversacional (E18/ADR-019).

Contrato estable con el proxy BFF SvelteKit (`/asistente/api/message`) y, por
encima, con el `api-gateway`. El texto de respuesta es SIEMPRE Markdown (nunca
HTML): el saneamiento/renderizado vive en el proxy del frontend, no aquí.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class AssistantMessageRequest(BaseModel):
    """Turno entrante del usuario."""

    mensaje: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)


class AssistantTurn(BaseModel):
    """Turno saliente del asistente."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str = "assistant"
    texto: str
    formato: str = "markdown"  # el backend NUNCA envía HTML (RF-FIR/E18 §5)
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class AssistantMessageResponse(BaseModel):
    conversation_id: str
    message: AssistantTurn
