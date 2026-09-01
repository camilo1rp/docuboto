# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de plantillas y borradores de Salida (paridad legado)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PlantillaCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=150)
    cuerpo: str = Field(..., min_length=1)
    tipo_documental: str | None = Field(default=None, max_length=100)
    activo: bool = True


class PlantillaResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    tipo_documental: str | None
    cuerpo: str
    activo: bool
    created_at: datetime


class CombinarRequest(BaseModel):
    """Contexto con el que combinar una plantilla antes de editarla."""

    radicado_padre_id: uuid.UUID | None = None
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)


class CombinarResponse(BaseModel):
    cuerpo: str
    faltantes: list[str] = Field(default_factory=list)


class BorradorCreate(BaseModel):
    doc_type: Literal["S", "I"] = "S"
    subject: str = Field(..., min_length=1, max_length=2000)
    dest_dept: str | None = Field(default=None, max_length=100)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    cuerpo: str | None = None
    plantilla_id: uuid.UUID | None = None
    #: Radicado de Entrada que este borrador responde. Es la fuente de la mitad
    #: del contexto de combinación (E23, RF-PRO-02); sin él, `*RAD_E*` y
    #: `*NOM_R*` no tienen de dónde salir.
    radicado_padre_id: uuid.UUID | None = None


class BorradorUpdate(BaseModel):
    subject: str | None = Field(default=None, max_length=2000)
    dest_dept: str | None = Field(default=None, max_length=100)
    cuerpo: str | None = None


class BorradorResponse(BaseModel):
    id: uuid.UUID
    doc_type: str
    subject: str
    dest_dept: str | None
    cuerpo: str | None
    plantilla_id: uuid.UUID | None
    radicado_padre_id: uuid.UUID | None = None
    estado: str
    radicado_id: uuid.UUID | None
    creado_por: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    #: Marcadores del vocabulario que quedaron SIN valor al combinar (RF-PRO-01).
    #: No se persiste: se calcula en el acto de combinar y viaja para que la
    #: interfaz los enseñe. Un oficio que sale con un campo vacío en silencio es
    #: el defecto que esta lista existe para evitar.
    faltantes: list[str] = Field(default_factory=list)
