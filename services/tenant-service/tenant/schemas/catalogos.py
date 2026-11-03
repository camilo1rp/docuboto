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
from typing import Literal

from pydantic import BaseModel, Field

# Ámbito = a qué acto pertenece una causal. Solo `causales` lo tiene (ver
# CON_AMBITO en el repositorio); en el resto de catálogos viaja como None.
Ambito = Literal[
    "devolucion",
    "anulacion",
    "reasignacion",
    "devolucion_postal",
    "general",
]


class CatalogoItemCreate(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=10)
    nombre: str = Field(..., min_length=1, max_length=100)
    activo: bool = True
    ambito: Ambito | None = None


class CatalogoItemUpdate(BaseModel):
    nombre: str | None = Field(None, min_length=1, max_length=100)
    activo: bool | None = None
    ambito: Ambito | None = None


class CatalogoItemResponse(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    activo: bool
    # None en los catálogos que no distinguen acto — el campo existe siempre en
    # la respuesta para que el cliente no tenga que saber cuál es cuál.
    ambito: Ambito | None = None
