# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de firma electrónica (E06, ADR-016)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SignatureCreate(BaseModel):
    content_hash: str = Field(..., pattern=r"^[0-9a-fA-F]{64}$", description="SHA-256 hex")
    anexo_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class SignatureResponse(BaseModel):
    id: uuid.UUID
    radicado_id: uuid.UUID
    anexo_id: uuid.UUID | None
    signer_id: uuid.UUID | None
    signer_name: str | None
    content_hash: str
    reason: str | None
    signed_at: datetime


class SignatureVerifyResponse(BaseModel):
    content_hash: str
    valid: bool
    signatures: list[SignatureResponse]
