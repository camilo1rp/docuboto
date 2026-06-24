# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de plantillas y elementos de metadatos (E03, ADR-007)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

_TIPO_DATO = Literal["text", "number", "date", "boolean", "select"]


class MetadataTemplateCreate(BaseModel):
    tipo_documental: str = Field(..., min_length=1, max_length=100)
    json_schema: dict[str, Any] = Field(
        ..., description="JSON Schema de los campos de metadato válidos"
    )
    version: int = Field(default=1, ge=1)
    activo: bool = True


class MetadataTemplateResponse(BaseModel):
    id: uuid.UUID
    tipo_documental: str
    version: int
    json_schema: dict[str, Any]
    activo: bool
    created_at: datetime


class MetadataElementCreate(BaseModel):
    clave: str = Field(..., min_length=1, max_length=100)
    etiqueta: str = Field(..., min_length=1, max_length=200)
    tipo_dato: _TIPO_DATO
    longitud: int | None = Field(default=None, ge=1)
    ocurrencia_min: int = Field(default=0, ge=0)
    ocurrencia_max: int | None = Field(default=None, ge=1)
    modificable: bool = True
    valor_default: Any = None
    opciones: list[Any] | None = None
    orden: int = 0
    searchable: bool = False
    mapeo_dublin_core: str | None = Field(default=None, max_length=100)
    activo: bool = True


class MetadataElementUpdate(BaseModel):
    etiqueta: str | None = Field(default=None, min_length=1, max_length=200)
    tipo_dato: _TIPO_DATO | None = None
    longitud: int | None = Field(default=None, ge=1)
    ocurrencia_min: int | None = Field(default=None, ge=0)
    ocurrencia_max: int | None = Field(default=None, ge=1)
    modificable: bool | None = None
    valor_default: Any = None
    opciones: list[Any] | None = None
    orden: int | None = None
    searchable: bool | None = None
    mapeo_dublin_core: str | None = Field(default=None, max_length=100)
    activo: bool | None = None


class MetadataElementResponse(BaseModel):
    id: uuid.UUID
    clave: str
    etiqueta: str
    tipo_dato: str
    longitud: int | None
    ocurrencia_min: int
    ocurrencia_max: int | None
    modificable: bool
    valor_default: Any = None
    opciones: list[Any] | None = None
    orden: int
    searchable: bool
    mapeo_dublin_core: str | None
    activo: bool
    created_at: datetime
