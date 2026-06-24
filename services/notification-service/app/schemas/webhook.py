# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de suscripciones de webhooks (E11, ADR-018)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class WebhookCreate(BaseModel):
    url: HttpUrl
    event_types: list[str] = Field(default_factory=list)  # vacío = todos
    secret: str | None = Field(default=None, max_length=255)


class WebhookResponse(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    active: bool
    created_at: datetime
