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

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.core.config import settings
from app.core.webhook_security import validate_webhook_target


class WebhookCreate(BaseModel):
    url: HttpUrl
    event_types: list[str] = Field(default_factory=list)  # vacío = todos
    secret: str | None = Field(default=None, max_length=255)

    @field_validator("url")
    @classmethod
    def _reject_internal_target(cls, value: HttpUrl) -> HttpUrl:
        """Rechaza destinos internos/no enrutables (hallazgo Media SSRF).

        Validación RÁPIDA sin resolución DNS (`resolve=False`) — ver el
        docstring de `app.core.webhook_security` para por qué la resolución
        completa se hace en cada intento de entrega y no aquí.
        """
        try:
            validate_webhook_target(
                str(value),
                allow_private=settings.webhook_allow_private_targets,
                resolve=False,
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value


class WebhookResponse(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    active: bool
    created_at: datetime
