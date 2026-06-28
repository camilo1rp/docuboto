# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas Pydantic v2 — cadena de firma (E06, ADR-016)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SolicitudCreate(BaseModel):
    """Request body to create a signature chain for a document object."""

    objeto_tipo: str = Field(..., min_length=1, max_length=40)
    objeto_id: str = Field(..., min_length=1)
    titulo: str | None = None
    # SHA-256 hex digest of the document content (64 lowercase hex chars).
    # When present, each signing turn produces a firma bound to this digest.
    payload_sha256: str | None = None
    # At least one signer required; the requester's identity comes from JWT (RF-FIR-11).
    firmantes: list[uuid.UUID] = Field(..., min_length=1)

    @field_validator("payload_sha256")
    @classmethod
    def validate_sha256_hex(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("payload_sha256 must be exactly 64 lowercase hex characters")
        return v


class TurnoItem(BaseModel):
    """Single turn within a signature chain."""

    model_config = ConfigDict(from_attributes=True)

    orden: int
    firmante_id: uuid.UUID
    estado: str
    firma_id: uuid.UUID | None = None
    comentario: str | None = None
    decidido_at: datetime | None = None


class SolicitudResponse(BaseModel):
    """Full signature chain: header + ordered turns."""

    solicitud_id: uuid.UUID
    objeto_tipo: str
    objeto_id: str
    titulo: str | None
    estado: str
    items: list[TurnoItem]
    created_at: datetime


class PendienteItem(BaseModel):
    """Row in the signer's inbox — solicitudes awaiting this user's turn."""

    solicitud_id: uuid.UUID
    objeto_tipo: str
    objeto_id: str
    titulo: str | None
    orden: int
    created_at: datetime


class PendientesResponse(BaseModel):
    """Paginated list of pending signature requests for the current user."""

    items: list[PendienteItem]
    total: int
    page: int
    size: int


class RechazoRequest(BaseModel):
    """Rejection with a mandatory reason (motivo)."""

    motivo: str = Field(..., min_length=1)


class FirmarRequest(BaseModel):
    """
    Optional signing payload.

    - If the solicitud already carries payload_sha256, that hash takes priority.
    - Otherwise, if payload_xml is provided, its SHA-256 is used.
    - Fallback: sign over "{objeto_tipo}:{objeto_id}".
    """

    payload_xml: str | None = None


class LoteRequest(BaseModel):
    """Batch sign: a list of solicitud IDs the caller wants to sign in one call."""

    solicitud_ids: list[uuid.UUID] = Field(..., min_length=1)


class ResultadoLoteItem(BaseModel):
    """Outcome of a single solicitud in a batch-sign operation."""

    solicitud_id: uuid.UUID
    resultado: Literal["firmado", "omitido"]
    detalle: str | None = None


class LoteResponse(BaseModel):
    """Aggregated result of a batch-sign operation."""

    resultados: list[ResultadoLoteItem]
