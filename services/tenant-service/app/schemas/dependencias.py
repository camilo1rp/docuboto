# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de dependencias (organigrama) por tenant (E14)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DependenciaCreate(BaseModel):
    codigo: int = Field(..., ge=1, le=999999, description="depe_codi (3-4 dígitos)")
    sigla: str = Field(..., min_length=1, max_length=20)
    nombre: str = Field(..., min_length=1, max_length=500)
    parent_id: uuid.UUID | None = None
    depe_rad_tp2: bool = False
    activo: bool = True


class DependenciaUpdate(BaseModel):
    """Actualización parcial: solo se aplican los campos presentes."""

    codigo: int | None = Field(None, ge=1, le=999999)
    sigla: str | None = Field(None, min_length=1, max_length=20)
    nombre: str | None = Field(None, min_length=1, max_length=500)
    parent_id: uuid.UUID | None = None
    depe_rad_tp2: bool | None = None
    activo: bool | None = None


class DependenciaResponse(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    codigo: int
    sigla: str
    nombre: str
    depe_rad_tp2: bool
    activo: bool
    created_at: datetime
    updated_at: datetime


class DependenciaNode(DependenciaResponse):
    """Nodo del árbol jerárquico (incluye sus hijos)."""

    children: list["DependenciaNode"] = []
