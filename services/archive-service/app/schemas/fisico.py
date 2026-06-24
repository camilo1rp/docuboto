# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas del archivo físico (E17, ADR-017)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

_NIVEL = Literal[
    "sede",
    "edificio",
    "piso",
    "deposito",
    "modulo",
    "estante",
    "entrepano",
    "gaveta",
    "custodia_externa",
]
_TIPO_UNIDAD = Literal["caja", "carpeta", "legajo", "libro", "tomo", "az", "folder"]


class UbicacionCreate(BaseModel):
    nivel: _NIVEL
    codigo: str = Field(..., min_length=1, max_length=30)
    nombre: str = Field(..., min_length=1)
    parent_id: uuid.UUID | None = None
    tipo_archivo: Literal["gestion", "central", "historico"] | None = None
    capacidad_ml: float | None = Field(default=None, ge=0)


class UbicacionResponse(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    nivel: str
    codigo: str
    nombre: str
    tipo_archivo: str | None
    capacidad_ml: float | None
    activo: bool
    created_at: datetime
    ruta: str | None = None


class UnidadCreate(BaseModel):
    tipo: _TIPO_UNIDAD
    codigo: str = Field(..., min_length=1, max_length=30)
    ubicacion_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    soporte: str | None = Field(default=None, max_length=30)
    num_folios: int | None = Field(default=None, ge=0)


class UnidadResponse(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    ubicacion_id: uuid.UUID | None
    tipo: str
    codigo: str
    signatura: str | None
    soporte: str | None
    num_folios: int | None
    activo: bool
    created_at: datetime


class ExpedienteUnidadLink(BaseModel):
    expediente_id: uuid.UUID
    folio_inicio: int | None = Field(default=None, ge=1)
    folio_fin: int | None = Field(default=None, ge=1)


class UnidadDeExpediente(BaseModel):
    """Dónde está el expediente: unidad + signatura + rango."""

    unidad_id: uuid.UUID
    signatura: str | None
    ubicacion_id: uuid.UUID | None
    folio_inicio: int | None
    folio_fin: int | None


class ExpedienteEnUnidad(BaseModel):
    """Qué contiene la unidad: expedientes + rango."""

    expediente_id: uuid.UUID
    folio_inicio: int | None
    folio_fin: int | None


class PrestamoCreate(BaseModel):
    prestado_a: str = Field(..., min_length=1, max_length=255)
    prestado_por: str | None = Field(default=None, max_length=255)
    fecha_devolucion_esperada: date


class PrestamoResponse(BaseModel):
    id: uuid.UUID
    unidad_id: uuid.UUID
    prestado_a: str
    prestado_por: str | None
    fecha_prestamo: datetime
    fecha_devolucion_esperada: date
    fecha_devolucion_real: datetime | None
    estado: str
    vencido: bool


class MovimientoResponse(BaseModel):
    id: uuid.UUID
    unidad_id: uuid.UUID
    accion: str
    ubicacion_before: uuid.UUID | None
    ubicacion_after: uuid.UUID | None
    performed_by: str | None
    ts: datetime


class FuidRow(BaseModel):
    """Fila del Formato Único de Inventario Documental (AGN 042/2002)."""

    orden: int
    expediente_id: uuid.UUID
    expediente_code: str
    asunto: str
    serie_code: str | None
    serie_name: str | None
    signatura: str | None
    unidad_tipo: str
    soporte: str | None
    num_folios: int | None
    folio_inicio: int | None
    folio_fin: int | None
    fecha_inicial: datetime
    fecha_final: datetime | None


class FuidResponse(BaseModel):
    generado_ts: datetime
    total: int
    rows: list[FuidRow]
