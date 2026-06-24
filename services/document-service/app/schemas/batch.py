# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas for batch API operations."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BatchDocumentCreate(BaseModel):
    """Single document in batch create request."""

    doc_type: str = Field(..., description="E (Entrada), S (Salida), I (Interno)")
    subject: str
    doc_class: str | None = None
    dest_dept: str | None = None
    sender_name: str | None = None
    sender_entity: str | None = None
    pages: int | None = None
    observations: str | None = None


class BatchDocumentsRequest(BaseModel):
    """Batch create documents request."""

    documents: list[BatchDocumentCreate] = Field(
        ..., min_length=1, max_length=1000, description="1-1000 documents"
    )


class BatchJobResponse(BaseModel):
    """Response when batch job is created."""

    job_id: UUID
    job_type: str
    status: str = "pending"
    total_items: int
    created_at: datetime


class BatchItemResult(BaseModel):
    """Result of a single item in batch."""

    item_index: int
    status: str  # 'success' or 'failed'
    result_id: UUID | None = None  # UUID of created radicado/expediente
    error_message: str | None = None


class BatchJobStatusResponse(BaseModel):
    """Status of a batch job."""

    job_id: UUID
    job_type: str
    status: str  # pending, processing, completed, failed
    total_items: int
    processed_items: int
    failed_items: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    items: list[BatchItemResult] = Field(default_factory=list)
    error_message: str | None = None

    @property
    def progress_percent(self) -> int:
        """Calculate progress percentage."""
        if self.total_items == 0:
            return 0
        return int((self.processed_items / self.total_items) * 100)


class BatchExpedienteCreate(BaseModel):
    """Single expediente in batch create request."""

    name: str
    description: str | None = None
    trd_serie_id: UUID | None = None
    radicado_ids: list[UUID] = Field(
        default_factory=list, description="Radicados to link to this expediente"
    )


class BatchExpedientesRequest(BaseModel):
    """Batch create expedientes request."""

    expedientes: list[BatchExpedienteCreate] = Field(
        ..., min_length=1, max_length=100, description="1-100 expedientes"
    )
