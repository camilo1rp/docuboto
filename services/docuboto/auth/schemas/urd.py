# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas URD y contexto de dependencia (E08)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class URDCreate(BaseModel):
    depe_id: int = Field(..., ge=1, le=999999)
    rol_id: uuid.UUID
    es_principal: bool = False


class URDResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    depe_id: int
    rol_id: uuid.UUID
    es_principal: bool
    is_active: bool
    created_at: datetime


class ContextSwitchRequest(BaseModel):
    depe_id: int = Field(..., ge=1, le=999999)


class ContextResponse(BaseModel):
    user_id: uuid.UUID
    active_depe_id: int | None
    available_depts: list[URDResponse]
