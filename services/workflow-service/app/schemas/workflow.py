# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FlowStepCreate(BaseModel):
    radicado_id: uuid.UUID
    tracking_number: str = Field(..., min_length=1, max_length=50)
    to_dept: str = Field(..., min_length=1, max_length=100)
    assigned_to: uuid.UUID | None = None
    action: Literal["assign", "transfer", "return", "close"]
    notes: str | None = None
    from_dept: str | None = None  # required for transfer/return, validated in service
    doc_type: str | None = None  # E/S/I del radicado, para bandejas tipadas (RF-FLU-03)
    due_at: datetime | None = None  # plazo del paso (RF-FLU-07)


class FlowStepComplete(BaseModel):
    notes: str | None = None


class FlowStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    radicado_id: uuid.UUID
    tracking_number: str
    step_number: int
    from_dept: str | None
    to_dept: str
    assigned_to: uuid.UUID | None
    assigned_by: uuid.UUID
    action: str
    notes: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    doc_type: str | None = None
    due_at: datetime | None = None
    semaforo: str | None = None  # verde | amarillo | rojo | vencido (RF-FLU-07)


class FlowHistoryResponse(BaseModel):
    radicado_id: uuid.UUID
    tracking_number: str
    steps: list[FlowStepResponse]


class FlowEventResponse(BaseModel):
    id: uuid.UUID
    radicado_id: uuid.UUID
    tracking_number: str
    tipo_tx: str
    from_dept: str | None
    to_dept: str | None
    actor: uuid.UUID | None
    comentario: str | None
    detalles: dict
    ts: datetime


class TransactionTypeResponse(BaseModel):
    code: str
    nombre: str
    descripcion: str | None = None
    requires_permission: str | None = None
    target_step_status: str | None = None
    activo: bool = True


class TransactionRequest(BaseModel):
    tipo_tx: str = Field(..., min_length=1, max_length=40)
    comentario: str | None = None
    detalles: dict = Field(default_factory=dict)


class RollbackRequest(BaseModel):
    """Motivo de la reversión de la última asignación (RF-FLU-08).

    Obligatorio: el modal del frontend (`RevertirModal.svelte`) ya lo exige antes
    de habilitar el botón — hacerlo opcional en el backend dejaría esa exigencia
    sin efecto (el motivo se perdería en el camino, como pasaba antes de este
    contrato). A diferencia de `AnulacionDecision.observacion` (opcional, señalado
    como debilidad): aquí el invariante "toda reversión declara por qué" se fija
    donde manda, en el backend.
    """

    motivo: str = Field(..., min_length=5, max_length=500)


class DevolucionRequest(BaseModel):
    to_dept: str = Field(..., min_length=1, max_length=100)
    causal: str = Field(..., min_length=1, max_length=15)
    comentario: str | None = None


class CascadeReassignRequest(BaseModel):
    from_assigned_to: uuid.UUID | None = None
    from_dept: str | None = None
    to_assigned_to: uuid.UUID

    @model_validator(mode="after")
    def _at_least_one_source(self) -> CascadeReassignRequest:
        if self.from_assigned_to is None and self.from_dept is None:
            raise ValueError("Indique from_assigned_to o from_dept")
        return self


class CascadeReassignResponse(BaseModel):
    reassigned: int


class PendingListResponse(BaseModel):
    items: list[FlowStepResponse]
    total: int


class HealthResponse(BaseModel):
    status: str
    database: str
