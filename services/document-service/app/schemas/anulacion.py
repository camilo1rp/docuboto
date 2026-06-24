# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de anulación de radicado en dos pasos (paridad legado)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AnulacionSolicitud(BaseModel):
    causal: str = Field(..., min_length=1, max_length=15)
    motivo: str | None = Field(default=None, max_length=2000)


class AnulacionDecision(BaseModel):
    observacion: str | None = Field(default=None, max_length=2000)


class AnulacionResponse(BaseModel):
    id: uuid.UUID
    radicado_id: uuid.UUID
    estado: str
    causal: str
    motivo: str | None
    solicitado_por: uuid.UUID | None
    solicitado_at: datetime
    decidido_por: uuid.UUID | None
    decidido_at: datetime | None
    observacion: str | None
