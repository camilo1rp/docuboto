# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de configuración por tenant (E14)."""

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class HolidayCreate(BaseModel):
    fecha: date
    descripcion: str = Field(..., min_length=1, max_length=200)
    tipo: Literal["festivo", "puente", "especial"] = "festivo"
    is_national: bool = True


class HolidayResponse(BaseModel):
    id: uuid.UUID
    fecha: date
    descripcion: str
    tipo: str
    is_national: bool


class HolidaySeedRequest(BaseModel):
    year_from: int = Field(..., ge=2000, le=2100)
    year_to: int = Field(..., ge=2000, le=2100)


class BusinessDaysResult(BaseModel):
    from_date: date
    business_days: int
    due_date: date


class ParamResponse(BaseModel):
    key: str
    value: Any


class ParamUpdate(BaseModel):
    value: Any
