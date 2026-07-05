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

from pydantic import BaseModel, Field, model_validator


class BatchExpedienteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    trd_serie_id: UUID | None = None
    radicado_ids: list[UUID] = Field(default_factory=list)
    tracking_numbers: list[str] = Field(default_factory=list)
    # E15 perfil v4 (R1): dependencia productora por-radicado, alineada por
    # ÍNDICE con `radicado_ids` (igual que `tracking_numbers`) — listas
    # paralelas opcionales; un índice fuera de rango o `None` en su posición
    # significa "no declarada" para ese radicado (ver `batch_service.py`).
    dependencia_productora_codigos: list[int | None] = Field(default_factory=list)
    dependencia_productora_nombres: list[str | None] = Field(default_factory=list)

    @model_validator(mode="after")
    def _productora_codigo_y_nombre_van_juntos(self) -> "BatchExpedienteCreate":
        """Espejo exacto de `RadicadoLink._productora_codigo_y_nombre_van_juntos`
        (schemas/archive.py): rechaza con 422 — ANTES de tocar la BD — el par
        parcial código/nombre en cualquier índice, en vez de dejar que lo
        ataje el CHECK de pareja de la migración 019 (que solo se dispara ya
        dentro de la transacción de `batch_service.py`). También valida que,
        si se aporta, cada lista tenga la misma longitud que `radicado_ids`
        (son paralelas por índice — ver `batch_service.py`)."""
        for lista, nombre in (
            (self.dependencia_productora_codigos, "dependencia_productora_codigos"),
            (self.dependencia_productora_nombres, "dependencia_productora_nombres"),
        ):
            if lista and len(lista) != len(self.radicado_ids):
                raise ValueError(
                    f"{nombre} debe tener la misma longitud que radicado_ids "
                    "cuando se aporta (E15 perfil v4, R1)"
                )
        for i in range(max(len(self.dependencia_productora_codigos), len(self.dependencia_productora_nombres))):
            has_codigo = (
                i < len(self.dependencia_productora_codigos)
                and self.dependencia_productora_codigos[i] is not None
            )
            has_nombre = (
                i < len(self.dependencia_productora_nombres)
                and self.dependencia_productora_nombres[i] is not None
            )
            if has_codigo != has_nombre:
                raise ValueError(
                    "dependencia_productora_codigo y dependencia_productora_nombre "
                    "deben aportarse juntos o ninguno (E15 perfil v4, R1)"
                )
        return self


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
