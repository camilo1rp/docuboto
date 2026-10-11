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
    #: `True` = la ve cualquier dependencia. Se llama `es_global` y no `global`
    #: porque esa palabra es reservada en Python (E23, RF-PRO-06).
    es_global: bool = True
    #: Dependencias que la alcanzan cuando NO es global. Vacía sobre una
    #: plantilla no global significa que no la ve nadie — un estado válido:
    #: una plantilla retirada de circulación sin borrar su historial.
    dependencias: list[int] = Field(default_factory=list)


class PlantillaResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    tipo_documental: str | None
    cuerpo: str
    activo: bool
    created_at: datetime
    es_global: bool = True
    creado_por: uuid.UUID | None = None
    dependencias: list[int] = Field(default_factory=list)


class PlantillaAlcanceUpdate(BaseModel):
    """Cambia a quién alcanza una plantilla, sin tocar su contenido."""

    es_global: bool
    dependencias: list[int] = Field(default_factory=list)


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


class BorradorRadicar(BaseModel):
    """Datos mínimos del radicado que el borrador no guarda (Ac. AGN 060/2001 §5.2).

    Todos opcionales: lo que no se envíe se deriva (tipo de la plantilla, dependencia
    destino por nombre, dependencia de origen de quien radica). Lo que se envíe MANDA
    sobre la derivación, para que quien radica pueda elegir cuando hay más de una.
    """

    tipo_documental: str | None = Field(default=None, max_length=100)
    origin_dept_code: int | None = Field(default=None, ge=1, le=999999)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)


class BorradorUpdate(BaseModel):
    subject: str | None = Field(default=None, max_length=2000)
    #: ALIAS deprecado de `dest_dept_code` (ADR-032): se resuelve a código.
    dest_dept: str | None = Field(default=None, max_length=100)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    cuerpo: str | None = None


class BorradorResponse(BaseModel):
    id: uuid.UUID
    doc_type: str
    subject: str
    #: Instantánea del nombre al elegir el destino; la llave es `dest_dept_code`.
    dest_dept: str | None
    dest_dept_code: int | None = None
    cuerpo: str | None
    plantilla_id: uuid.UUID | None
    radicado_padre_id: uuid.UUID | None = None
    #: Heredado del radicado padre (RF-SEG-08); la barrera de residencia del
    #: asistente recorta el borrador por este campo.
    nivel_seguridad: int
    estado: str
    radicado_id: uuid.UUID | None
    creado_por: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    #: El PDF/A producido, cuando ya se generó (E23, RF-PRO-03).
    documento_file_id: uuid.UUID | None = None
    documento_sha256: str | None = None
    documento_formato: str | None = None
    #: Marcadores del vocabulario que quedaron SIN valor al combinar (RF-PRO-01).
    #: No se persiste: se calcula en el acto de combinar y viaja para que la
    #: interfaz los enseñe. Un oficio que sale con un campo vacío en silencio es
    #: el defecto que esta lista existe para evitar.
    faltantes: list[str] = Field(default_factory=list)
