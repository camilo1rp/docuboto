# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas Pydantic v2 — firma personal PKI por-usuario (E17/F4, Incremento 1).

Ninguno de estos modelos expone JAMÁS la clave privada — ni en claro, ni
cifrada, ni cualquier derivado. A diferencia de ``TotpEnrollResponse``
(revela el secreto una sola vez), :class:`SigningKeyEnrollResponse` nunca
revela la clave — solo el CSR (material público) y el estado.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_TOTP_CODE_RE = re.compile(r"\d{6}")


def _validate_totp_code(v: str) -> str:
    if not _TOTP_CODE_RE.fullmatch(v):
        raise ValueError("totp_code must be exactly 6 digits")
    return v


class SigningKeyEnrollResponse(BaseModel):
    """``POST /me/signing-key/enroll`` — el CSR es material PÚBLICO; la clave
    privada NUNCA aparece aquí ni en ningún otro response de este router."""

    estado: Literal["pendiente_emision"] = "pendiente_emision"
    csr_pem: str


class SigningKeyStatusResponse(BaseModel):
    """``GET /me/signing-key`` — NUNCA incluye la clave privada. ``cert_pem``
    es público (el mismo certificado que cualquier verificador de la firma
    XAdES resultante vería embebido en ``ds:KeyInfo``)."""

    estado: Literal["no_configurado", "pendiente_emision", "activo", "revocado"]
    cert_pem: str | None = None
    serial: str | None = None
    subject_dn: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    activated_at: datetime | None = None


class InternalSignDigestRequest(BaseModel):
    """Body de ``POST /internal/signing-key/sign-digest`` (signature-service
    ÚNICAMENTE, servicio-a-servicio). La identidad del firmante se deriva
    SIEMPRE del Bearer reenviado (D1, cierra IDOR) — este body NUNCA lleva un
    ``user_id``.

    ``digest_b64`` es el SHA-256 de ``C14N-exclusiva(ds:SignedInfo)`` — NUNCA
    el documento completo (ver ``app.providers.local`` en signature-service,
    técnica validada en el Paso 0 del incremento). ``prehashed=True`` es
    obligatorio y documenta la forma del esquema (RSASSA-PKCS1-v1_5 sobre un
    digest ya calculado, no sobre datos crudos) — reservado para un futuro
    algoritmo distinto, hoy el único soportado.
    """

    digest_b64: str = Field(..., min_length=1)
    alg: Literal["RSA-SHA256"] = "RSA-SHA256"
    prehashed: Literal[True] = True
    totp_code: str = Field(...)

    @field_validator("totp_code")
    @classmethod
    def _validate_totp(cls, v: str) -> str:
        return _validate_totp_code(v)


class InternalActivateSigningKeyRequest(BaseModel):
    """Body de ``POST /internal/signing-key/activate`` — carga del
    certificado emitido OFFLINE (``scripts/gen-user-cert.sh``) sobre la
    credencial ``pendiente_emision`` de ``user_id``. Operación
    administrativa/de sistema (gate SOLO ``X-Internal-Token`` — no hay un
    Bearer de usuario interactivo detrás; el operador que emitió el
    certificado ya validó el CSR fuera de banda antes de llamar aquí)."""

    user_id: uuid.UUID
    cert_pem: str = Field(..., min_length=1)


class InternalSignDigestResponse(BaseModel):
    """Respuesta de ``POST /internal/signing-key/sign-digest`` — SOLO
    material público: la firma cruda (no la clave), el certificado y su
    huella. NUNCA la clave privada, ni siquiera cifrada."""

    signature_value_b64: str
    certificado_pem: str
    certificado_fingerprint: str
    serial: str | None = None
    not_after: datetime | None = None
