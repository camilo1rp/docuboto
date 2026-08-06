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

from app.core.config import settings
from app.core.constants import CADENA_OBJETO_TIPOS


class SolicitudCreate(BaseModel):
    """Request body to create a signature chain for a document object."""

    objeto_tipo: str = Field(..., min_length=1, max_length=40)
    objeto_id: str = Field(..., min_length=1)
    titulo: str | None = None
    # NOTA: `objeto_tipo` se valida contra `CADENA_OBJETO_TIPOS` más abajo. La
    # comparación es EXACTA y sensible a mayúsculas a propósito: el servicio
    # decide con ella si consulta la fuente autoritativa del nivel de seguridad,
    # así que aceptar variantes ("Radicado") equivaldría a dejar que el llamante
    # elija si le aplican el control.
    # SHA-256 hex digest of the document content (64 lowercase hex chars).
    # When present, each signing turn produces a firma bound to this digest.
    payload_sha256: str | None = None
    # At least one signer required; the requester's identity comes from JWT (RF-FIR-11).
    firmantes: list[uuid.UUID] = Field(..., min_length=1)
    # Security level override for non-radicado objects (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA).
    # For objeto_tipo='radicado', the level is resolved from radicados.nivel_seguridad
    # and this field is ignored. For other types, defaults to 1 (PUBLICA) when omitted.
    nivel_seguridad: int | None = Field(default=None, ge=1, le=3)

    @field_validator("objeto_tipo")
    @classmethod
    def validate_objeto_tipo(cls, v: str) -> str:
        if v not in CADENA_OBJETO_TIPOS:
            raise ValueError(f"objeto_tipo debe ser uno de {sorted(CADENA_OBJETO_TIPOS)}")
        return v

    @field_validator("payload_sha256")
    @classmethod
    def validate_sha256_hex(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("payload_sha256 must be exactly 64 lowercase hex characters")
        return v


def _validate_otp_digits(v: str) -> str:
    """Shared OTP-format check, derived from settings.otp_longitud (config-driven,
    not a literal 6) so that emission (app/core/otp.py generate_otp) and
    verification (this validator) can never drift apart — e.g. a deployment
    that sets otp_longitud=8 would otherwise reject every emitted OTP here
    (422) and lock out the personal-signature 2FA path entirely.
    """
    length = settings.otp_longitud
    if not re.fullmatch(rf"\d{{{length}}}", v):
        raise ValueError(f"otp must be exactly {length} digits")
    return v


_TOTP_CODE_RE = re.compile(r"\d{6}")


def _validate_totp_digits(v: str) -> str:
    """TOTP codes (RFC 6238, D6 of the auth-service contract) are ALWAYS 6
    digits — fixed by the RFC 6238 profile chosen there (HMAC-SHA1, 6 digits,
    30s period), unlike the OTP-email code above whose length is a local
    config knob (``otp_longitud``). The two must never be conflated.
    """
    if not _TOTP_CODE_RE.fullmatch(v):
        raise ValueError("totp_code must be exactly 6 digits")
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
    nivel_seguridad: int
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

    Second factor (E06, RF-FIR-13) — two alternative satisfactors, mutually
    exclusive by construction in CadenaFirmaService._gate_2fa:
      - challenge_id/otp: OTP-correo (Inc.2). Required together.
      - totp_code: TOTP (RFC 6238), verified against auth-service. When both
        are present, TOTP takes priority and the OTP-correo fields are
        ignored (see _gate_2fa's deterministic factor selection).
    """

    payload_xml: str | None = None
    challenge_id: uuid.UUID | None = None
    otp: str | None = None
    totp_code: str | None = None

    @field_validator("otp")
    @classmethod
    def validate_otp_format(cls, v: str | None) -> str | None:
        return _validate_otp_digits(v) if v is not None else v

    @field_validator("totp_code")
    @classmethod
    def validate_totp_format(cls, v: str | None) -> str | None:
        return _validate_totp_digits(v) if v is not None else v


class ChallengeResponse(BaseModel):
    """2FA challenge emitted for the caller's active turn (RF-FIR-13).

    The OTP itself NEVER appears here — only metadata about the delivered
    challenge. `canal` is already masked (e.g. 'j***@dominio.gov.co').
    """

    challenge_id: uuid.UUID
    factor_tipo: Literal["otp_email"] = "otp_email"
    canal: str
    expira_at: datetime


class LoteItem(BaseModel):
    """One batch item: its own solicitud + its own 2FA credential (Q2 — one
    credential per document, never reused across items).

    ``challenge_id``/``otp`` (OTP-correo) and ``totp_code`` (RFC 6238) are
    now BOTH optional — CadenaFirmaService._gate_2fa enforces "exactly one
    factor, TOTP wins if both present, 409 if neither" per item, same as
    FirmarRequest for a single sign. Kept optional (not "exactly one of")
    at the schema level so the error surfaces as the existing per-item
    'omitido: 2fa_requerido' outcome rather than a blanket 422 for the whole
    batch.
    """

    solicitud_id: uuid.UUID
    challenge_id: uuid.UUID | None = None
    otp: str | None = None
    totp_code: str | None = None

    @field_validator("otp")
    @classmethod
    def validate_otp_format(cls, v: str | None) -> str | None:
        return _validate_otp_digits(v) if v is not None else v

    @field_validator("totp_code")
    @classmethod
    def validate_totp_format(cls, v: str | None) -> str | None:
        return _validate_totp_digits(v) if v is not None else v


class LoteRequest(BaseModel):
    """Batch sign: each item carries its own challenge_id + otp (BREAKING, Q2)."""

    items: list[LoteItem] = Field(..., min_length=1)


class ResultadoLoteItem(BaseModel):
    """Outcome of a single solicitud in a batch-sign operation."""

    solicitud_id: uuid.UUID
    resultado: Literal["firmado", "omitido"]
    detalle: str | None = None


class LoteResponse(BaseModel):
    """Aggregated result of a batch-sign operation."""

    resultados: list[ResultadoLoteItem]
