# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de envíos postales (E20)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

_OPERADOR = Literal["4-72", "servientrega", "otro"]
_ESTADO = Literal["registrado", "en_transito", "entregado", "devuelto", "fallido"]


class ShipmentCreate(BaseModel):
    operador: _OPERADOR
    destinatario: str | None = Field(default=None, max_length=255)
    direccion: str | None = Field(default=None, max_length=500)


class ShipmentStatusUpdate(BaseModel):
    estado: _ESTADO
    descripcion: str | None = Field(default=None, max_length=500)


class ShipmentEvent(BaseModel):
    estado: str
    descripcion: str | None
    ts: datetime


class ShipmentResponse(BaseModel):
    id: uuid.UUID
    radicado_id: uuid.UUID
    operador: str
    guia: str
    estado: str
    destinatario: str | None
    direccion: str | None
    created_at: datetime
    updated_at: datetime
    eventos: list[ShipmentEvent] = []
