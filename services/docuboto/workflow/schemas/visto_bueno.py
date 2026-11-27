# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de vistos buenos secuenciales (paridad legado)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CadenaCreate(BaseModel):
    revisores: list[uuid.UUID] = Field(..., min_length=1, max_length=20)


class VoBoDecision(BaseModel):
    aprobar: bool
    comentario: str | None = Field(default=None, max_length=2000)


class VistoBuenoItem(BaseModel):
    orden: int
    revisor_id: uuid.UUID
    estado: str
    comentario: str | None
    decidido_at: datetime | None


class CadenaResponse(BaseModel):
    radicado_id: uuid.UUID
    estado: str  # en_revision | aprobado | rechazado
    items: list[VistoBuenoItem]
