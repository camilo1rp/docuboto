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

from pydantic import BaseModel, Field, field_validator

from app.schemas.document import rechazar_claves_reservadas


class RespuestaRapidaCreate(BaseModel):
    subject: str | None = Field(default=None, max_length=2000)
    dest_dept: str | None = Field(default=None, max_length=100)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    #: Ac. AGN 060/2001 §5.2: una Salida exige origen y tipo documental. Si se
    #: omiten, hereda los del antecedente: la dependencia que RECIBIÓ la Entrada
    #: es la que responde, y el tipo se toma del antecedente hasta que se tipifique.
    origin_dept_code: int | None = Field(default=None, ge=1, le=999999)
    doc_class: str | None = Field(default=None, max_length=100)
    observations: str | None = None
    response_days: int | None = Field(default=None, ge=1, le=3650)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_sin_claves_reservadas(cls, v: dict[str, Any]) -> dict[str, Any]:
        return rechazar_claves_reservadas(v) or {}


class RespuestaItem(BaseModel):
    id: uuid.UUID
    tracking_number: str
    doc_type: str
    subject: str
    #: Declarado: la barrera de residencia del asistente recorta por este campo.
    nivel_seguridad: int
    status: str
    registered_at: datetime
