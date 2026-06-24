# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas for batch expediente operations."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BatchExpedienteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    trd_serie_id: UUID | None = None
    radicado_ids: list[UUID] = Field(default_factory=list)
    tracking_numbers: list[str] = Field(default_factory=list)


class BatchExpedientesRequest(BaseModel):
    expedientes: list[BatchExpedienteCreate] = Field(..., min_length=1, max_length=100)


class BatchJobResponse(BaseModel):
    job_id: UUID
    job_type: str
    status: str = "pending"
    total_items: int
    created_at: datetime


class BatchItemResult(BaseModel):
    item_index: int
    status: str
    result_id: UUID | None = None
    expediente_code: str | None = None
    error_message: str | None = None


class BatchJobStatusResponse(BaseModel):
    job_id: UUID
    job_type: str
    status: str
    total_items: int
    processed_items: int
    failed_items: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    items: list[BatchItemResult] = Field(default_factory=list)
    error_message: str | None = None
