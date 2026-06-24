# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de catálogos de referencia por tenant (E14)."""

import uuid

from pydantic import BaseModel, Field


class CatalogoItemCreate(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=10)
    nombre: str = Field(..., min_length=1, max_length=100)
    activo: bool = True


class CatalogoItemUpdate(BaseModel):
    nombre: str | None = Field(None, min_length=1, max_length=100)
    activo: bool | None = None


class CatalogoItemResponse(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    activo: bool
