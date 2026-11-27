# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de plantillas de metadatos del expediente (E03, ADR-007)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ExpedienteTemplateCreate(BaseModel):
    trd_serie_id: uuid.UUID
    json_schema: dict[str, Any] = Field(
        ..., description="JSON Schema de los metadatos válidos del expediente"
    )
    version: int = Field(default=1, ge=1)
    activo: bool = True


class ExpedienteTemplateResponse(BaseModel):
    id: uuid.UUID
    trd_serie_id: uuid.UUID
    version: int
    json_schema: dict[str, Any]
    activo: bool
    created_at: datetime
