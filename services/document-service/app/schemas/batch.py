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

from pydantic import BaseModel, Field, model_validator

from app.core.datos_minimos import faltantes, mensaje_faltantes


class BatchDocumentCreate(BaseModel):
    """Single document in batch create request."""

    doc_type: str = Field(..., description="E (Entrada), S (Salida), I (Interno)")
    subject: str
    doc_class: str | None = None
    dest_dept: str | None = None
    #: Códigos del organigrama (Ac. AGN 060/2001 §5.2, AREA-09-02): E e I exigen
    #: el destino, S exige el origen. Sin ellos el lote era una segunda puerta
    #: por la que entraba lo que `POST /documents` rechaza.
    origin_dept_code: int | None = Field(default=None, ge=1, le=999999)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    sender_name: str | None = None
    sender_entity: str | None = None
    pages: int | None = None
    observations: str | None = None

    @model_validator(mode="after")
    def _require_datos_minimos(self) -> "BatchDocumentCreate":
        falta = faltantes(
            self.doc_type,
            doc_class=self.doc_class,
            origin_dept_code=self.origin_dept_code,
            dest_dept_code=self.dest_dept_code,
        )
        if falta:
            raise ValueError(mensaje_faltantes(self.doc_type, falta))
        return self


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
    status: str  # 'pending' | 'success' | 'failed'
    result_id: UUID | None = None  # UUID of created radicado/expediente
    error_message: str | None = None
    #: Clase de la excepción del último fallo (`ValidationError`, `RuntimeError`...).
    error_tipo: str | None = None
    #: Veces que se intentó el ítem (el primer intento cuenta 1).
    intentos: int = 0
    #: Asunto del documento del ítem: lo que la pantalla muestra junto al error.
    subject: str | None = None
    #: Nivel de seguridad del radicado creado (1..3); `null` si el ítem aún no tiene
    #: radicado o este supera el nivel de acceso del llamante (el `subject` también
    #: viene `null` en ese caso).
    nivel_seguridad: int | None


class BatchJobStatusResponse(BaseModel):
    """Status of a batch job."""

    job_id: UUID
    job_type: str
    #: pending | processing | completed | completado_con_errores | failed
    #: (`failed` = el lote reventó fuera de los ítems; con ítems fallidos el lote
    #: TERMINA como `completado_con_errores`).
    status: str
    total_items: int
    processed_items: int
    failed_items: int
    #: Ítems aún sin procesar (faltantes: lote interrumpido).
    pending_items: int = 0
    #: `true` si el lote terminó con ítems fallidos/faltantes que `POST
    #: /batch/documents/{id}/reprocesar` puede retomar («Procesar los faltantes»).
    reprocesable: bool = False
    reanudaciones: int = 0
    reprocesado_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    items: list[BatchItemResult] = Field(default_factory=list)
    #: Subconjunto de `items` con `status == 'failed'`, con su motivo.
    errores: list[BatchItemResult] = Field(default_factory=list)
    error_message: str | None = None

    @property
    def progress_percent(self) -> int:
        """Calculate progress percentage."""
        if self.total_items == 0:
            return 0
        return int((self.processed_items / self.total_items) * 100)


class BatchReprocesarResponse(BaseModel):
    """Respuesta de `POST /api/v1/batch/documents/{job_id}/reprocesar` (202)."""

    job_id: UUID
    status: str = "pending"
    #: Ítems que se van a procesar: los fallidos devueltos a pendiente + los faltantes.
    items_a_procesar: int


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
