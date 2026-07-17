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


class SigningKeyEnrollRequest(BaseModel):
    """Body de ``POST /me/signing-key/enroll`` (E17/F4, Incremento 2).

    ``step_up_totp_code`` es requerido SOLO cuando ya existe una credencial
    ``activo`` (re-enrolar la revoca de inmediato — cierra el residual
    step-up de Incremento 1) — verificado-y-consumido vía ``TotpService``,
    igual que ``load_and_sign_digest``/``DELETE /me/signing-key``. Sin
    credencial previa, o con una ``pendiente_emision`` (nunca firmó nada),
    NO se exige — mismo criterio que antes de este incremento.
    """

    step_up_totp_code: str | None = Field(default=None)

    @field_validator("step_up_totp_code")
    @classmethod
    def _validate_step_up(cls, v: str | None) -> str | None:
        return _validate_totp_code(v) if v is not None else v


class SigningKeyEnrollResponse(BaseModel):
    """``POST /me/signing-key/enroll`` — el CSR es material PÚBLICO; la clave
    privada NUNCA aparece aquí ni en ningún otro response de este router."""

    estado: Literal["pendiente_emision"] = "pendiente_emision"
    csr_pem: str


class SigningKeyRevokeRequest(BaseModel):
    """Body de ``DELETE /me/signing-key`` (self-revoke, E17/F4, Incremento 2).

    ``step_up_totp_code`` es SIEMPRE requerido (a diferencia del re-enroll,
    que solo lo exige sobre una credencial ``activo``): revocar es un acto
    destructivo explícito sobre la propia capacidad de firma, gateado por el
    segundo factor del titular — verificado-y-consumido vía ``TotpService``,
    igual que ``load_and_sign_digest``. ``motivo`` es texto libre opcional,
    nunca material de firma.
    """

    step_up_totp_code: str = Field(...)
    motivo: str | None = Field(default=None, max_length=500)

    @field_validator("step_up_totp_code")
    @classmethod
    def _validate_step_up(cls, v: str) -> str:
        return _validate_totp_code(v)


class SigningKeyRevokeResponse(BaseModel):
    estado: Literal["revocado"] = "revocado"


class AdminSigningKeyRevokeRequest(BaseModel):
    """Body de ``POST /admin/users/{user_id}/signing-key/revoke`` — gateado
    por ``USUA_PERM_ADMIN`` (router-level), sin step-up TOTP propio (el
    permiso administrativo ya es el gate; el TOTP del admin, si lo tiene, no
    es relevante para revocar la clave de OTRO usuario)."""

    motivo: str | None = Field(default=None, max_length=500)


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
    huella. NUNCA la clave privada, ni siquiera cifrada.

    ``ca_cert_pem``/``ca_fingerprint`` (E17/F4, Incremento 2 — Baja #4): el
    certificado PÚBLICO de la sub-CA de usuarios que emitió ESTE leaf y su
    huella — persistidos en ``auth_user_signing_key`` desde
    ``activar_certificado`` (nunca releídos del filesystem aquí), para que
    signature-service pueda embeber ``xades:CertificateValues`` de la sub-CA
    en la firma y anclar ``firma.ca_fingerprint`` sin tener que confiar en
    cualquier CA que el llamante le presente. ``None`` para una fila
    'activo' emitida ANTES de este incremento (grandfather estructural, sin
    ``user_ca_fingerprint`` que resolver) — el llamante entonces produce una
    firma personal SIN anclaje, idéntica en forma a las de Incremento 1.
    """

    signature_value_b64: str
    certificado_pem: str
    certificado_fingerprint: str
    serial: str | None = None
    not_after: datetime | None = None
    ca_cert_pem: str | None = None
    ca_fingerprint: str | None = None


class SigningKeyRevocationStatusResponse(BaseModel):
    """``GET /internal/signing-key/revocation-status`` (signature-service,
    D-02) — SOLO el estado de revocación por huella de certificado LEAF.
    NUNCA el certificado, la clave (ni cifrada) o cualquier otro material —
    ver el docstring del router para el contrato completo.

    ``desconocido`` (fail-closed): la huella no se encontró en ESTE tenant
    (otro tenant, forjada, o el usuario titular fue borrado por CASCADE) —
    NUNCA se reporta ``vigente`` en ese caso.
    """

    status: Literal["vigente", "revocado", "desconocido"]
    revocado_at: datetime | None = None
