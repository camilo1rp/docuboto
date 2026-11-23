# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas Pydantic v2 del PINAR (RF-ADM-08, E14). Acuerdo AGN 003/2015."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

# ── Plan ──────────────────────────────────────────────────────────────────────


class PlanCreate(BaseModel):
    vision: str | None = None
    vigencia_desde: date | None = None
    vigencia_hasta: date | None = None
    acto_administrativo: str | None = Field(None, max_length=200)


class PlanUpdate(BaseModel):
    """Solo editable en estado 'borrador' (invariante 1)."""

    vision: str | None = None
    vigencia_desde: date | None = None
    vigencia_hasta: date | None = None


class PlanAprobar(BaseModel):
    acto_administrativo: str = Field(..., min_length=1, max_length=200)


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    vigencia_desde: date | None
    vigencia_hasta: date | None
    vision: str | None
    acto_administrativo: str | None
    estado: str
    aprobado_por: uuid.UUID | None
    aprobado_at: datetime | None
    created_at: datetime


# ── Aspectos críticos ─────────────────────────────────────────────────────────


class AspectoCreate(BaseModel):
    descripcion: str = Field(..., min_length=1)
    riesgo: str | None = None
    orden: int = 0


class AspectoUpdate(BaseModel):
    descripcion: str | None = Field(None, min_length=1)
    riesgo: str | None = None
    orden: int | None = None


class AspectoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    descripcion: str
    riesgo: str | None
    orden: int


# ── Ejes + priorización ───────────────────────────────────────────────────────


class EjeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: int
    nombre: str


class PriorizacionCelda(BaseModel):
    eje_code: int = Field(..., ge=1, le=5)
    impacto: int = Field(..., ge=1, le=10)


class PriorizacionSet(BaseModel):
    """Matriz aspecto × eje. Reemplaza por completo la priorización del aspecto."""

    aspecto_id: uuid.UUID
    celdas: list[PriorizacionCelda]


class PriorizacionResult(BaseModel):
    """Aspecto con su prioridad calculada = Σ impactos sobre los ejes (desc)."""

    aspecto_id: uuid.UUID
    descripcion: str
    prioridad: int
    impactos: dict[int, int]  # eje_code -> impacto


# ── Objetivos ─────────────────────────────────────────────────────────────────


class ObjetivoCreate(BaseModel):
    descripcion: str = Field(..., min_length=1)
    orden: int = 0


class ObjetivoUpdate(BaseModel):
    descripcion: str | None = Field(None, min_length=1)
    orden: int | None = None


class ObjetivoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    descripcion: str
    orden: int


# ── Proyectos ─────────────────────────────────────────────────────────────────


class ProyectoCreate(BaseModel):
    objetivo_id: uuid.UUID | None = None
    nombre: str = Field(..., min_length=1, max_length=200)
    meta: str | None = None
    indicador: str | None = None
    responsable: str | None = Field(None, max_length=200)
    recurso: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None


class ProyectoUpdate(BaseModel):
    objetivo_id: uuid.UUID | None = None
    nombre: str | None = Field(None, min_length=1, max_length=200)
    meta: str | None = None
    indicador: str | None = None
    responsable: str | None = Field(None, max_length=200)
    recurso: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    estado: str | None = Field(None, pattern="^(planeado|en_curso|terminado|cancelado)$")


class ProyectoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    objetivo_id: uuid.UUID | None
    nombre: str
    meta: str | None
    indicador: str | None
    responsable: str | None
    recurso: str | None
    fecha_inicio: date | None
    fecha_fin: date | None
    estado: str
    avance_pct: int


# ── Seguimiento (append-only) ─────────────────────────────────────────────────


class SeguimientoCreate(BaseModel):
    fecha: date
    avance_pct: int = Field(..., ge=0, le=100)
    observacion: str | None = None


class SeguimientoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proyecto_id: uuid.UUID
    fecha: date
    avance_pct: int
    observacion: str | None
    registrado_por: uuid.UUID | None
    created_at: datetime


# ── Articulación con otros instrumentos ───────────────────────────────────────


class InstrumentoRefCreate(BaseModel):
    tipo: str = Field(..., pattern="^(ccd|trd|fuid|plan_preservacion|plan_contingencia)$")
    ref: str = Field(..., min_length=1, max_length=200)
    descripcion: str | None = None


class InstrumentoRefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    tipo: str
    ref: str
    descripcion: str | None


# ── Tableros ──────────────────────────────────────────────────────────────────


class TableroObjetivo(BaseModel):
    objetivo_id: uuid.UUID | None
    descripcion: str | None
    avance_promedio: float
    proyectos: int


class TableroEje(BaseModel):
    eje_code: int
    nombre: str
    avance_promedio: float
    proyectos: int


class TableroResponse(BaseModel):
    plan_id: uuid.UUID
    por_objetivo: list[TableroObjetivo]
    por_eje: list[TableroEje]
