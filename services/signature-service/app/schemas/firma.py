# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas del signature-service (E06, ADR-016)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SignXmlRequest(BaseModel):
    payload_xml: str = Field(..., min_length=1)
    objeto_tipo: str = Field(..., min_length=1, max_length=40)
    objeto_id: str | None = None
    formato: str | None = Field(default="XAdES", max_length=20)
    firmante_id: uuid.UUID | None = None


class FirmaResponse(BaseModel):
    firma_id: uuid.UUID
    objeto_tipo: str
    objeto_id: str | None
    formato_solicitado: str | None
    payload_sha256: str
    provider: str
    created_at: datetime


class VerifyRequest(BaseModel):
    firma_id: uuid.UUID
    payload_xml: str = Field(..., min_length=1)


class VerifyResponse(BaseModel):
    firma_id: uuid.UUID
    valida: bool
    provider: str
