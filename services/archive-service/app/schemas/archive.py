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
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExpedienteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    trd_serie_id: uuid.UUID | None = None
    # Nivel de seguridad / clasificación (RF-SEG-08): 1=PUBLICA, 2=RESERVADA,
    # 3=CLASIFICADA (= security_levels.code). Por defecto PUBLICA. La búsqueda de
    # expedientes oculta a cada usuario los de nivel superior a su clearance.
    nivel_seguridad: int = Field(default=1, ge=1, le=3)
    # Metadatos variables del expediente (E03, ADR-007). Si la serie TRD tiene
    # plantilla activa, se validan contra su JSON Schema al crear.
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExpedienteUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trd_serie_id: uuid.UUID | None = None
    status: Literal["open", "closed", "transferred"] | None = None


class RadicadoLink(BaseModel):
    radicado_id: uuid.UUID
    tracking_number: str = Field(..., min_length=1, max_length=50)
    # SHA-256 del documento (E15 T-08): se materializa como valor_huella del índice.
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class BatchLinkRequest(BaseModel):
    radicados: list[RadicadoLink] = Field(..., min_length=1, max_length=500)


class BatchLinkResult(BaseModel):
    vinculados: int
    omitidos: int  # ya estaban vinculados
    total: int


class ExpedienteRadicadoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    radicado_id: uuid.UUID
    tracking_number: str
    added_at: datetime


class ExpedienteEventoResponse(BaseModel):
    """Evento de la hoja de ruta append-only del expediente (E02 §9)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    expediente_id: uuid.UUID
    numero_expediente: str | None = None
    accion: str
    actor: uuid.UUID | None = None
    comentario: str | None = None
    detalles: dict[str, Any] = Field(default_factory=dict)
    ts: datetime


class ExpedienteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    trd_serie_id: uuid.UUID | None
    status: str
    opened_by: uuid.UUID | None
    opened_at: datetime
    closed_at: datetime | None
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    disposition: dict[str, Any] | None = None
    radicados: list[ExpedienteRadicadoResponse] = []


# Disposición: códigos AGN (CT/E/S/M) + valores antiguos por compatibilidad.
_DISPOSITION = Literal["CT", "E", "S", "M", "eliminate", "conserve", "digitize", "select"]


class TrdSerieCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1)
    description: str | None = None
    retention_years: int = 5
    total_retention: int = 10
    disposition: _DISPOSITION = "E"
    # E04/ADR-015: subserie (parent_id) + retención en dos fases.
    parent_id: uuid.UUID | None = None
    archivo_gestion_years: int | None = Field(default=None, ge=0)
    archivo_central_years: int | None = Field(default=None, ge=0)


class TrdSerieUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    retention_years: int | None = None
    total_retention: int | None = None
    disposition: str | None = None
    is_active: bool | None = None


class TrdSerieResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    retention_years: int
    total_retention: int
    disposition: str
    is_active: bool
    created_at: datetime
    parent_id: uuid.UUID | None = None
    archivo_gestion_years: int | None = None
    archivo_central_years: int | None = None
    version: int = 1
    valid_from: date | None = None


class IndiceItem(BaseModel):
    folio: int
    radicado_id: uuid.UUID
    tracking_number: str
    added_at: datetime


class ExpedienteIndice(BaseModel):
    expediente_id: uuid.UUID
    code: str
    total_folios: int
    items: list[IndiceItem]


class IndiceItemResponse(BaseModel):
    document_id: uuid.UUID
    orden: int
    titulo: str | None = None
    tipologia: str | None = None
    fecha_archivado: datetime | None = None
    valor_huella: str | None = None
    funcion_resumen: str = "SHA-256"


class IndiceResponse(BaseModel):
    id: uuid.UUID
    expediente_id: uuid.UUID
    version: int
    estado: str
    num_documentos: int
    xml_sha256: str
    algoritmo: str
    firma_id: uuid.UUID | None = None
    generado_ts: datetime
    items: list[IndiceItemResponse] = []


class IndiceVersionSummary(BaseModel):
    version: int
    estado: str
    num_documentos: int
    xml_sha256: str
    generado_ts: datetime
    generado_por: str | None = None


class RebuildResult(BaseModel):
    version: int
    xml_sha256: str
    num_documentos: int
    unchanged: bool = False


class IndiceVerifyDoc(BaseModel):
    document_id: uuid.UUID
    huella_indice: str | None
    huella_actual: str | None
    coincide: bool


class IndiceVerifyFirma(BaseModel):
    presente: bool
    valida: bool


class IndiceVerifyResponse(BaseModel):
    version: int
    valido: bool
    documentos: list[IndiceVerifyDoc]
    firma: IndiceVerifyFirma
    fecha_verificacion: datetime


class RetentionSchedule(BaseModel):
    serie_id: uuid.UUID
    code: str
    closed_at: date
    archivo_gestion_years: int
    archivo_central_years: int
    fin_archivo_gestion: date
    fin_archivo_central: date
    disposition_code: str
    disposition_label: str


class TipoDocumentalCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    nombre: str = Field(..., min_length=1)
    trd_serie_id: uuid.UUID | None = None
    obligatorio: bool = False
    metadata_template: str | None = Field(default=None, max_length=100)
    activo: bool = True


class TipoDocumentalUpdate(BaseModel):
    nombre: str | None = None
    trd_serie_id: uuid.UUID | None = None
    obligatorio: bool | None = None
    metadata_template: str | None = None
    activo: bool | None = None


class TipoDocumentalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trd_serie_id: uuid.UUID | None
    code: str
    nombre: str
    obligatorio: bool
    metadata_template: str | None
    activo: bool
    created_at: datetime


class ExpedienteSearchHit(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    opened_at: datetime
    rank: float


class ExpedienteSearchResponse(BaseModel):
    query: str | None = None
    total: int
    hits: list[ExpedienteSearchHit]
    page: int
    size: int


class HealthResponse(BaseModel):
    status: str
    database: str
