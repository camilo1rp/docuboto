# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnexoInput(BaseModel):
    file_id: uuid.UUID
    filename: str = Field(..., min_length=1, max_length=255)
    file_size: int | None = None
    mime_type: str | None = None
    tipo_anexo: str | None = Field(default=None, max_length=50)
    es_principal: bool = False
    folios: int | None = Field(default=None, ge=0)


class DocumentCreate(BaseModel):
    doc_type: Literal["E", "S", "I"]
    subject: str = Field(..., min_length=1, max_length=2000)
    doc_class: str | None = Field(default=None, max_length=100)
    origin_dept: str | None = Field(default=None, max_length=100)
    dest_dept: str | None = Field(default=None, max_length=100)
    # Código (depe_codi) de la dependencia en el organigrama. Si se indica,
    # document-service lo valida contra tenant-service y denormaliza el nombre
    # de la dependencia en origin_dept/dest_dept (RF-RAD: vínculo con dependencias).
    origin_dept_code: int | None = Field(default=None, ge=1, le=999999)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    sender_name: str | None = Field(default=None, max_length=255)
    sender_entity: str | None = Field(default=None, max_length=255)
    pages: int = Field(default=0, ge=0)
    observations: str | None = None
    # Nivel de seguridad / clasificación (RF-SEG-08): 1=PUBLICA, 2=RESERVADA,
    # 3=CLASIFICADA (= security_levels.code). Por defecto PUBLICA. La búsqueda (E09)
    # oculta a cada usuario los radicados por encima de su clearance (RF-BUS-04).
    nivel_seguridad: int = Field(default=1, ge=1, le=3)
    # Plazo de respuesta en DÍAS HÁBILES (RF-RAD-04). Si se indica, document-service
    # calcula la fecha de vencimiento vía tenant-service; si se omite, no hay plazo.
    response_days: int | None = Field(default=None, ge=1, le=3650)
    # Metadatos variables del tipo documental (E03, ADR-007). Si el doc_class tiene
    # una plantilla activa, se validan contra su JSON Schema antes de radicar.
    metadata: dict[str, Any] = Field(default_factory=dict)
    anexos: list[AnexoInput] = []


class DocumentUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=2000)
    dest_dept: str | None = Field(default=None, max_length=100)
    observations: str | None = None
    status: Literal["registered", "distributed", "in_progress", "closed"] | None = None
    pages: int | None = Field(default=None, ge=0)


class AnexoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    file_size: int | None
    mime_type: str | None
    created_at: datetime
    tipo_anexo: str | None = None
    es_principal: bool = False
    folios: int | None = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tracking_number: str
    doc_type: str
    year: int
    sequence: int
    subject: str
    doc_class: str | None
    origin_dept: str | None
    dest_dept: str | None
    origin_dept_code: int | None = None
    dest_dept_code: int | None = None
    sender_name: str | None
    sender_entity: str | None
    status: str
    pages: int
    nivel_seguridad: int
    observations: str | None
    registered_by: uuid.UUID | None
    registered_at: datetime
    updated_at: datetime
    due_date: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    anexos: list[AnexoResponse] = []


class DispositionUpdate(BaseModel):
    programa: str | None = Field(default=None, max_length=200)
    retention_until: date | None = None
    accion: Literal["conservar", "eliminar", "transferir", "seleccionar"] | None = None
    confirmado: bool | None = None
    marcado_eliminacion: bool | None = None


class DispositionResponse(BaseModel):
    radicado_id: uuid.UUID
    disposition: dict[str, Any]


class ReclassifyRequest(BaseModel):
    nivel_seguridad: int = Field(..., ge=1, le=3)
    # Ley 1712/2014 art.19 y 28: todo acto de clasificación/reserva debe ser motivado.
    motivo: str = Field(..., min_length=1, max_length=500)
    # Campos opcionales de fundamentación jurídica (Ley 1712/2014 art.18-19).
    # Se registran en audit_log (hash-chain) como soporte inmutable del acto —
    # no crean columnas nuevas; el JSONB de audit_log es el registro legal.
    causal_reserva: str | None = Field(default=None, max_length=200)
    fundamento_juridico: str | None = Field(default=None, max_length=300)
    plazo_reserva_meses: int | None = Field(default=None, ge=0, le=600)


class ReclassifyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tracking_number: str
    nivel_seguridad: int


class HealthResponse(BaseModel):
    status: str
    database: str
