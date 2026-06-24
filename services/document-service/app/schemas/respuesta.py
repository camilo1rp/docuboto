# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de respuesta rápida (antecedente Entrada↔Salida)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RespuestaRapidaCreate(BaseModel):
    subject: str | None = Field(default=None, max_length=2000)
    dest_dept: str | None = Field(default=None, max_length=100)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    observations: str | None = None
    response_days: int | None = Field(default=None, ge=1, le=3650)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RespuestaItem(BaseModel):
    id: uuid.UUID
    tracking_number: str
    doc_type: str
    subject: str
    status: str
    registered_at: datetime
