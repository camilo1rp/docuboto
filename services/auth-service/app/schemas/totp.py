# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas Pydantic v2 — TOTP (E06, RFC 6238).

Ninguno de estos modelos expone jamás el secreto TOTP — ni en claro ni cifrado.
``TotpEnrollResponse.otpauth_uri`` es la ÚNICA excepción intencional (revelado
una sola vez, al enrolar; el router agrega ``Cache-Control: no-store``)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_TOTP_CODE_RE = re.compile(r"\d{6}")


def _validate_totp_code(v: str) -> str:
    if not _TOTP_CODE_RE.fullmatch(v):
        raise ValueError("code must be exactly 6 digits")
    return v


class TotpEnrollRequest(BaseModel):
    """Body for ``POST /me/totp/enroll``.

    ``step_up_totp_code`` is required only when re-enrolling over an ALREADY
    active TOTP credential (prevents a hijacked session from silently
    swapping the secret) — see :class:`~app.services.totp_service.TotpService`.
    """

    step_up_totp_code: str | None = Field(default=None)
    # Código de recuperación (E06, migración 015): alternativa al TOTP para
    # quien perdió el dispositivo. NO comparte validador con el TOTP — su
    # formato es otro (20 caracteres alfanuméricos, guiones opcionales), y
    # reusar `_validate_totp_code` rechazaría todos los códigos válidos.
    step_up_recovery_code: str | None = Field(default=None, max_length=64)

    @field_validator("step_up_totp_code")
    @classmethod
    def _validate_step_up(cls, v: str | None) -> str | None:
        return _validate_totp_code(v) if v is not None else v


class TotpEnrollResponse(BaseModel):
    otpauth_uri: str
    estado: Literal["pendiente_activacion"] = "pendiente_activacion"


class TotpActivateRequest(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        return _validate_totp_code(v)


class TotpActivateResponse(BaseModel):
    estado: Literal["activo"] = "activo"
    activated_at: datetime
    # Códigos de recuperación (E06, migración 015). Se muestran EXACTAMENTE
    # una vez, aquí: en la base solo queda su hash scrypt, así que no hay
    # ninguna ruta para volver a leerlos — solo para regenerarlos, lo que
    # invalida el lote anterior. `GET /me/totp` devuelve el CONTEO, nunca los
    # códigos.
    recovery_codes: list[str] = Field(default_factory=list)


class TotpStatusResponse(BaseModel):
    """``GET /me/totp`` — NEVER includes the secret, ciphertext, or nonce."""

    estado: Literal["no_configurado", "pendiente_activacion", "activo"]
    activated_at: datetime | None = None
    locked_until: datetime | None = None
    # Cuántos códigos de recuperación quedan SIN usar. Es un conteo, nunca los
    # códigos: la interfaz necesita poder avisar de que se están agotando (es
    # lo que separa a un usuario de quedarse encerrado si pierde el teléfono),
    # y para eso el número basta.
    recovery_codes_restantes: int = 0


class TotpRevokeRequest(BaseModel):
    """``step_up_totp_code`` required only when the current credential is 'activo'.

    ``step_up_recovery_code`` es la alternativa para quien perdió el
    dispositivo: exactamente el caso para el que revocar el TOTP existe.
    """

    step_up_totp_code: str | None = Field(default=None)
    step_up_recovery_code: str | None = Field(default=None, max_length=64)

    @field_validator("step_up_totp_code")
    @classmethod
    def _validate_step_up(cls, v: str | None) -> str | None:
        return _validate_totp_code(v) if v is not None else v


class TotpRevokeResponse(BaseModel):
    estado: Literal["revocado"] = "revocado"


class TotpRecoveryRegenerateRequest(BaseModel):
    """Body de ``POST /me/totp/recovery-codes``: regenera el lote.

    Exige step-up igual que revocar — un lote nuevo invalida el anterior, así
    que quien lo dispara desde una sesión secuestrada dejaría al titular sin
    su vía de recuperación. Vale cualquiera de los dos factores.
    """

    step_up_totp_code: str | None = Field(default=None)
    step_up_recovery_code: str | None = Field(default=None, max_length=64)

    @field_validator("step_up_totp_code")
    @classmethod
    def _validate_step_up(cls, v: str | None) -> str | None:
        return _validate_totp_code(v) if v is not None else v


class TotpRecoveryRegenerateResponse(BaseModel):
    """Los códigos, otra vez, UNA sola vez."""

    recovery_codes: list[str]


class InternalTotpVerifyRequest(BaseModel):
    """Body for the service-to-service ``POST /internal/totp/verify``.

    ``contexto`` is an optional free-form caller-supplied correlation id
    (signature-service sends the ``solicitud_id`` being signed) — a
    correlation id, NEVER a secret. On success it is recorded on the
    ``totp.verificado`` audit entry (see ``TotpService.verify``) so that
    entry can be tied back to the corresponding ``firma.turno_firmado``
    entry in signature-service by a shared identifier. Optional: its
    absence never blocks verification.
    """

    code: str
    contexto: str | None = None

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        return _validate_totp_code(v)


class InternalTotpVerifyResponse(BaseModel):
    valido: bool
    motivo: Literal["no_activo", "bloqueado", "codigo_incorrecto", "replay"] | None = None
    intentos_restantes: int | None = None
    locked_until: datetime | None = None
    timestep: int | None = None
    factor_tipo: Literal["totp"] | None = None
