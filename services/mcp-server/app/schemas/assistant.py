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


class PropuestaCampo(BaseModel):
    """Un dato del acto, tal y como se le enseña a la persona."""

    arg: str
    etiqueta: str
    valor: object
    tipo: str


class Propuesta(BaseModel):
    """Un acto de escritura descrito y firmado, pendiente de aprobación.

    **La redacta el catálogo, no el modelo.** `operacion` y `campos` salen de la
    declaración de la tool; el texto del asistente va aparte, en `message`. Si la
    tarjeta la escribiera el modelo podría describir un acto distinto del que va
    firmado en el token, y la persona estaría aprobando una cosa creyendo
    aprobar otra — ingeniería social sobre quien confirma.
    """

    id: str
    tool: str
    operacion: str
    campos: list[PropuestaCampo]
    expira_en: int
    token: str
    #: ¿El acto es irreversible? Lo decide el catálogo, no el modelo.
    #:
    #: El preparador ya lo calculaba y lo ponía en la propuesta, y su comentario
    #: explica para qué: «para que la tarjeta lo AVISE antes, no después». Este
    #: esquema lo descartaba en silencio —Pydantic ignora las claves que no
    #: declara— así que el aviso moría entre quien lo calcula y quien lo enseña.
    #: La persona descubría que el acto no se deshace al ver la frase que se le
    #: pedía teclear, es decir demasiado tarde para reconsiderar.
    irreversible: bool = False
    #: Frase exacta que hay que teclear para confirmar, cuando el acto la exige.
    #: Se muestra en la tarjeta para que se sepa de antemano lo que se va a pedir.
    frase_requerida: str | None = None


class AssistantMessageResponse(BaseModel):
    conversation_id: str
    message: AssistantTurn
    # Vacía en la inmensa mayoría de turnos. Añadirla es aditivo: un cliente que
    # no la lea sigue funcionando igual que antes, y por eso el contrato del
    # proxy BFF no necesita despliegue coordinado.
    propuestas: list[Propuesta] = Field(default_factory=list)
    #: Números de radicado del texto que NO aparecen en ningún resultado de
    #: consulta de la conversación (AREA-11-04): el modelo los escribió sin que el
    #: sistema los haya devuelto. Vacía en el caso normal; aditiva, como `propuestas`.
    no_verificados: list[str] = Field(default_factory=list)
