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
    motivo_devolucion: str | None = None
    created_at: datetime
    updated_at: datetime
    eventos: list[ShipmentEvent] = []


class PostalCallbackPayload(BaseModel):
    """Cuerpo del webhook ENTRANTE del operador postal (E20 F5).

    `operador` selecciona el secreto HMAC y el mapa de estados; `event_id` es la
    clave de idempotencia (dedup por `(operador, event_id)`); `estado_operador`
    es la etiqueta cruda del operador (se mapea a la máquina interna); `motivo`
    alimenta `motivo_devolucion` cuando el estado resuelve a `devuelto`.
    """

    operador: _OPERADOR
    guia: str = Field(max_length=60)
    estado_operador: str = Field(max_length=40)
    event_id: str = Field(min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=500)
    motivo: str | None = Field(default=None, max_length=500)


class CallbackResult(BaseModel):
    """Resultado del callback. `duplicate` = ya procesado (idempotente); `applied`
    = transición aplicada; `recorded_no_change` = notificación registrada sin
    cambiar el estado (misma etiqueta o transición no válida sobre un terminal)."""

    status: Literal["applied", "duplicate", "recorded_no_change"]
    shipment_id: uuid.UUID | None = None
    estado: str | None = None


class TrackingResponse(BaseModel):
    shipment_id: uuid.UUID
    operador: str
    guia: str
    estado_interno_actual: str
    estado_operador: str | None = None
    estado_interno_mapeado: str | None = None
    operador_conectado: bool = False
