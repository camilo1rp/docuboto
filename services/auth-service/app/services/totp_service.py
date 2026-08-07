# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
TotpService — orquesta el ciclo de vida del segundo factor TOTP (E06, RFC 6238).

D1 OWNERSHIP: el secreto vive en AUTH-SERVICE (credencial de identidad
durable, junto a ``auth_users``). signature-service NUNCA lo ve — solo llama
a ``POST /internal/totp/verify`` (ver ``app/routers/totp.py``).

INVARIANTE RECTOR: el secreto se cifra en reposo (AESGCM) y solo se descifra
en memoria dentro de este servicio, para el tiempo mínimo indispensable. Se
revela en claro (embebido en el ``otpauth://`` URI) EXACTAMENTE una vez, en la
respuesta de ``enroll`` — ``GET /me/totp`` jamás lo devuelve, y ningún log,
evento o entrada de auditoría lo contiene jamás.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

import asyncpg
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.totp import (
    TotpAttempt,
    build_aad,
    build_otpauth_uri,
    classify_totp_attempt,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
)
from app.repositories.rbac_repository import RBACRepository
from app.repositories.totp_repository import TotpRepository
from app.schemas.totp import (
    InternalTotpVerifyResponse,
    TotpActivateResponse,
    TotpEnrollResponse,
    TotpStatusResponse,
)

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "auth-service"


def _step_up_invalido() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": "step_up_invalido",
            "detail": "Invalid or missing step-up TOTP code",
            "status": 401,
        },
    )


class TotpService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._repo = TotpRepository(conn)
        self._rbac = RBACRepository(conn)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _decrypt(self, row: asyncpg.Record, *, tenant_slug: str, user_id: uuid.UUID) -> bytes:
        aad = build_aad(tenant_slug=tenant_slug, user_id=str(user_id))
        return decrypt_secret(
            row["secret_cifrado"],
            nonce=row["secret_nonce"],
            key=settings.totp_encryption_key_bytes(),
            aad=aad,
        )

    async def _create_pending(
        self, user_id: uuid.UUID, tenant_slug: str
    ) -> tuple[asyncpg.Record, bytes]:
        """Generates a fresh secret, encrypts it, and inserts a new
        ``pendiente_activacion`` row. Returns ``(row, raw_secret)`` — the raw
        secret exists only in this return value and the caller's local scope,
        long enough to build the ``otpauth://`` URI; it is never persisted or
        logged in cleartext.
        """
        secret = generate_secret()
        aad = build_aad(tenant_slug=tenant_slug, user_id=str(user_id))
        ciphertext, nonce = encrypt_secret(
            secret, key=settings.totp_encryption_key_bytes(), aad=aad
        )
        row = await self._repo.insert_pending(
            user_id=user_id,
            secret_cifrado=ciphertext,
            secret_nonce=nonce,
            key_version=1,
        )
        return row, secret

    async def _audit_event(
        self,
        *,
        user_id: uuid.UUID,
        tenant_slug: str,
        action: str,
        payload: dict | None = None,
    ) -> None:
        """Best-effort audit — NEVER includes the secret, ciphertext, or nonce."""
        if not _AUDIT_AVAILABLE:
            return
        try:
            await _audit.append(
                self._conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action=action,
                object_type="totp",
                object_ref=str(user_id),
                actor=str(user_id),
                payload=payload or {},
            )
        except Exception:  # noqa: BLE001 — best-effort, never fails the request.
            logger.warning("TOTP audit append failed (action=%s)", action, exc_info=True)

    def _assert_not_locked(self, row: asyncpg.Record) -> None:
        now = datetime.now(UTC)
        if row["locked_until"] is not None and row["locked_until"] > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={
                    "error": "totp_bloqueado",
                    "detail": "TOTP is locked after too many failed attempts",
                    "locked_until": row["locked_until"].isoformat(),
                    "status": 423,
                },
            )

    async def _bump_failure(self, row: asyncpg.Record) -> None:
        """Persists a failed attempt (activate() path) via a single atomic
        UPDATE (see ``TotpRepository.record_failure``) — never resets the
        counter itself; only a subsequent success (advance_timestep) does."""
        await self._repo.record_failure(
            row["id"],
            max_attempts=settings.totp_max_failed_attempts,
            lockout_seconds=settings.totp_lockout_seconds,
        )

    # ------------------------------------------------------------------
    # Enroll
    # ------------------------------------------------------------------

    async def enroll(
        self,
        user_id: uuid.UUID,
        tenant_slug: str,
        step_up_code: str | None,
    ) -> TotpEnrollResponse:
        existing = await self._repo.get_by_user(user_id)

        if existing is not None and existing["estado"] == "activo":
            if not step_up_code:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "totp_ya_activo",
                        "detail": "TOTP already active — step-up code required to re-enroll",
                        "status": 409,
                    },
                )
            secret = self._decrypt(existing, tenant_slug=tenant_slug, user_id=user_id)
            attempt = classify_totp_attempt(
                secret,
                step_up_code,
                last_timestep=existing["last_timestep"],
                period=existing["periodo"],
                digits=existing["digitos"],
                skew=settings.totp_skew_steps,
            )
            if attempt.outcome != "valid":
                raise _step_up_invalido()
            async with self._conn.transaction():
                # Consume the step-up code (anti-replay) before revoking — a
                # captured step-up code must not be reusable elsewhere either.
                await self._repo.advance_timestep(user_id, new_timestep=attempt.consumed_timestep)
                await self._repo.revoke(existing["id"])
                new_row, raw_secret = await self._create_pending(user_id, tenant_slug)
            action = "totp.reenrolado"
        elif existing is not None:
            # pendiente_activacion — no credential to protect yet, no step-up needed.
            async with self._conn.transaction():
                await self._repo.revoke(existing["id"])
                new_row, raw_secret = await self._create_pending(user_id, tenant_slug)
            action = "totp.reenrolado"
        else:
            new_row, raw_secret = await self._create_pending(user_id, tenant_slug)
            action = "totp.enrolado"

        user = await self._rbac.get_user(user_id)
        account_name = ((user["email"] or user["username"]) if user else None) or str(user_id)

        otpauth_uri = build_otpauth_uri(
            secret=raw_secret,
            issuer=settings.totp_issuer,
            account_name=account_name,
            digits=new_row["digitos"],
            period=new_row["periodo"],
        )

        await self._audit_event(user_id=user_id, tenant_slug=tenant_slug, action=action)
        return TotpEnrollResponse(otpauth_uri=otpauth_uri, estado="pendiente_activacion")

    # ------------------------------------------------------------------
    # Activate
    # ------------------------------------------------------------------

    async def activate(
        self, user_id: uuid.UUID, tenant_slug: str, code: str
    ) -> TotpActivateResponse:
        row = await self._repo.get_by_user(user_id)
        if row is None or row["estado"] != "pendiente_activacion":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "totp_no_pendiente",
                    "detail": "No pending TOTP enrollment for this user",
                    "status": 409,
                },
            )
        self._assert_not_locked(row)

        secret = self._decrypt(row, tenant_slug=tenant_slug, user_id=user_id)
        attempt = classify_totp_attempt(
            secret,
            code,
            last_timestep=row["last_timestep"],
            period=row["periodo"],
            digits=row["digitos"],
            skew=settings.totp_skew_steps,
        )
        if attempt.outcome != "valid":
            await self._bump_failure(row)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "codigo_invalido", "detail": "Invalid TOTP code", "status": 401},
            )

        async with self._conn.transaction():
            await self._repo.advance_timestep(user_id, new_timestep=attempt.consumed_timestep)
            await self._repo.activate(row["id"])

        await self._audit_event(user_id=user_id, tenant_slug=tenant_slug, action="totp.activado")

        activated = await self._repo.get_by_user(user_id)
        assert activated is not None
        return TotpActivateResponse(estado="activo", activated_at=activated["activated_at"])

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self, user_id: uuid.UUID) -> TotpStatusResponse:
        row = await self._repo.get_by_user(user_id)
        if row is None:
            return TotpStatusResponse(estado="no_configurado")
        return TotpStatusResponse(
            estado=row["estado"],
            activated_at=row["activated_at"],
            locked_until=row["locked_until"],
        )

    # ------------------------------------------------------------------
    # Revoke (self-service disenroll)
    # ------------------------------------------------------------------

    async def revoke_self(
        self, user_id: uuid.UUID, tenant_slug: str, step_up_code: str | None
    ) -> None:
        row = await self._repo.get_by_user(user_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "no_totp", "detail": "No TOTP credential to revoke", "status": 404},
            )
        if row["estado"] == "activo":
            if not step_up_code:
                raise _step_up_invalido()
            secret = self._decrypt(row, tenant_slug=tenant_slug, user_id=user_id)
            attempt = classify_totp_attempt(
                secret,
                step_up_code,
                last_timestep=row["last_timestep"],
                period=row["periodo"],
                digits=row["digitos"],
                skew=settings.totp_skew_steps,
            )
            if attempt.outcome != "valid":
                raise _step_up_invalido()
            async with self._conn.transaction():
                await self._repo.advance_timestep(user_id, new_timestep=attempt.consumed_timestep)
                await self._repo.revoke(row["id"])
        else:
            await self._repo.revoke(row["id"])

        await self._audit_event(user_id=user_id, tenant_slug=tenant_slug, action="totp.desenrolado")

    # ------------------------------------------------------------------
    # Internal verify (called by signature-service, service-to-service)
    # ------------------------------------------------------------------

    async def verify(
        self,
        user_id: uuid.UUID,
        tenant_slug: str,
        code: str,
        *,
        contexto: str | None = None,
    ) -> InternalTotpVerifyResponse:
        """Consumes ``code`` against the user's ACTIVE credential.

        Never idempotent: a code that verifies is atomically advanced
        (``last_timestep``) so it can never be replayed. Honors
        ``locked_until`` BEFORE comparing the code (RF-SEG fail-closed).

        ``contexto`` is the caller-supplied correlation id (signature-service
        sends its ``solicitud_id``, see ``InternalTotpVerifyRequest``) — a
        correlation id, NEVER a secret. Recorded on the ``totp.verificado``
        audit entry alongside ``timestep`` so it can be tied back to the
        corresponding ``firma.turno_firmado`` entry in signature-service by a
        shared identifier instead of actor + clock proximity alone.
        """
        row = await self._repo.get_by_user(user_id)
        if row is None or row["estado"] != "activo":
            return InternalTotpVerifyResponse(valido=False, motivo="no_activo")

        now = datetime.now(UTC)
        if row["locked_until"] is not None and row["locked_until"] > now:
            return InternalTotpVerifyResponse(
                valido=False, motivo="bloqueado", locked_until=row["locked_until"]
            )

        secret = self._decrypt(row, tenant_slug=tenant_slug, user_id=user_id)
        attempt = classify_totp_attempt(
            secret,
            code,
            last_timestep=row["last_timestep"],
            period=row["periodo"],
            digits=row["digitos"],
            skew=settings.totp_skew_steps,
        )

        if attempt.outcome == "valid":
            advanced = await self._repo.advance_timestep(user_id, new_timestep=attempt.consumed_timestep)
            if not advanced:
                # Lost the race to a concurrent verify — treat as replay, never as success.
                attempt = TotpAttempt("replay", attempt.timestep)

        if attempt.outcome == "valid":
            payload: dict = {"timestep": attempt.timestep}
            if contexto is not None:
                payload["contexto"] = contexto
            await self._audit_event(
                user_id=user_id,
                tenant_slug=tenant_slug,
                action="totp.verificado",
                payload=payload,
            )
            return InternalTotpVerifyResponse(
                valido=True, timestep=attempt.timestep, factor_tipo="totp"
            )

        # Anotado con el Literal del esquema y no inferido como `str`: sin
        # esto, cualquier motivo nuevo mal escrito llegaría a
        # `InternalTotpVerifyResponse` sin que nada lo cazara hasta ejecución.
        motivo: Literal["replay", "codigo_incorrecto"] = (
            "replay" if attempt.outcome == "replay" else "codigo_incorrecto"
        )
        failure_row = await self._repo.record_failure(
            row["id"],
            max_attempts=settings.totp_max_failed_attempts,
            lockout_seconds=settings.totp_lockout_seconds,
        )
        new_failed = failure_row["failed_attempts"]
        locked_until = failure_row["locked_until"]
        intentos_restantes = max(settings.totp_max_failed_attempts - new_failed, 0)

        await self._audit_event(
            user_id=user_id,
            tenant_slug=tenant_slug,
            action="totp.verificacion_fallida",
            payload={"motivo": motivo, "intentos_restantes": intentos_restantes},
        )
        return InternalTotpVerifyResponse(
            valido=False,
            motivo=motivo,
            intentos_restantes=intentos_restantes,
            locked_until=locked_until,
        )
