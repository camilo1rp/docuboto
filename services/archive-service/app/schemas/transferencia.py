# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de transferencias documentales (E12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TransferenciaCreate(BaseModel):
    tipo: Literal["primaria", "secundaria"]
    expediente_id: uuid.UUID
    ubicacion_origen_id: uuid.UUID | None = None
    ubicacion_destino_id: uuid.UUID | None = None
    observacion: str | None = Field(default=None, max_length=2000)


class TransferenciaDecision(BaseModel):
    observacion: str | None = Field(default=None, max_length=2000)


class TransferenciaResponse(BaseModel):
    id: uuid.UUID
    tipo: str
    estado: str
    expediente_id: uuid.UUID
    ubicacion_origen_id: uuid.UUID | None
    ubicacion_destino_id: uuid.UUID | None
    creada_por: str | None
    observacion: str | None
    created_at: datetime
    enviada_at: datetime | None
    recibida_at: datetime | None
    decidida_por: str | None
