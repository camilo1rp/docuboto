# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Lo que signature pide a auth: correo del firmante, segundo factor y clave personal.

Eran rutas `/internal/totp/*` y `/internal/signing-key/*` con doble gate:
`X-Internal-Token` (el llamante es un servicio) y la identidad derivada del
bearer reenviado. En un solo proceso el token sobra (ADR-047 §1) y la
identidad ya la resolvió `UsuarioActualMiddleware` contra `auth_users`: llega
como `user_id`, nunca de un campo que el cliente controle. Lo que queda es lo
que auth comprueba en la BD: que la persona existe, que el TOTP es suyo y
vale, que la clave es suya y está activa.
"""

from __future__ import annotations

import logging
import uuid
from base64 import b64encode
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import HTTPException
from orpycamcp_common.database import acquire_tenant
from pydantic import ValidationError

from auth.repositories.rbac_repository import RBACRepository
from auth.schemas.signing_key import InternalSignDigestRequest, InternalSignDigestResponse
from auth.schemas.totp import (
    InternalTotpStatusResponse,
    InternalTotpVerifyRequest,
    InternalTotpVerifyResponse,
)
from auth.services.signing_key_service import SigningKeyService
from auth.services.totp_service import TotpService

logger = logging.getLogger("auth.api")


class AuthUnavailableError(RuntimeError):
    """No se pudo resolver lo pedido. Fail-closed: el llamante responde 503.

    No poder saber si alguien tiene TOTP o clave activa no es «no la tiene»:
    leerlo así bajaría el factor exigido en cuanto la BD fallara.
    """


class SignDigestError(Exception):
    """Respuesta de NEGOCIO de la firma remota (422 sin clave activa, 401 TOTP
    incorrecto, 423 bloqueado, 409 certificado cambiado). El llamante la mapea a
    su propio código."""

    def __init__(self, status_code: int, detail: dict[str, Any]) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"sign-digest -> {status_code}: {detail}")


@asynccontextmanager
async def _tenant_conn(tenant_slug: str) -> AsyncIterator[asyncpg.Connection]:
    try:
        async with acquire_tenant(tenant_slug) as conn:
            yield conn
    except (asyncpg.PostgresError, OSError) as exc:
        raise AuthUnavailableError(str(exc)) from exc


async def _existe(conn: asyncpg.Connection, user_id: uuid.UUID) -> bool:
    return await RBACRepository(conn).get_user(user_id) is not None


async def user_email(*, tenant_slug: str, user_id: uuid.UUID) -> str:
    """Correo de la persona: a él sale el OTP de su firma."""
    async with _tenant_conn(tenant_slug) as conn:
        user = await RBACRepository(conn).get_user(user_id)
    if user is None or not user["email"]:
        raise AuthUnavailableError("la persona no tiene correo registrado")
    return str(user["email"])


async def totp_status(*, tenant_slug: str, user_id: uuid.UUID) -> dict[str, Any]:
    """¿Tiene la persona un TOTP ACTIVO? (AREA-04-02). Content-free.

    Sin usuario o con el tenant sin aprovisionar: `activo=False` sin `user_id`
    (el llamante trata la falta de `user_id` como identidad no confirmada).
    """
    inactivo = InternalTotpStatusResponse(activo=False).model_dump(mode="json")
    async with _tenant_conn(tenant_slug) as conn:
        try:
            if not await _existe(conn, user_id):
                return inactivo
            estado = await TotpService(conn).status(user_id)
        except asyncpg.UndefinedTableError:
            logger.warning("totp_status: tenant '%s' sin aprovisionar; inactivo", tenant_slug)
            return inactivo
    return InternalTotpStatusResponse(
        activo=estado.estado == "activo", user_id=user_id
    ).model_dump(mode="json")


async def verify_totp(
    *, tenant_slug: str, user_id: uuid.UUID, code: str, contexto: str | None = None
) -> dict[str, Any]:
    """Verifica-y-consume un TOTP de la persona. Nunca idempotente (anti-replay).

    `contexto` es un identificador de correlación (la solicitud), nunca un
    secreto: ata el asiento `totp.verificado` al acto concreto. El código tiene
    que tener forma de TOTP: un código de recuperación no firma (RF-FIR-13).
    """
    try:
        pedido = InternalTotpVerifyRequest(code=code, contexto=contexto)
    except ValidationError as exc:
        raise AuthUnavailableError("el código no tiene la forma de un TOTP") from exc
    no_activo = InternalTotpVerifyResponse(valido=False, motivo="no_activo").model_dump(mode="json")
    async with _tenant_conn(tenant_slug) as conn:
        try:
            if not await _existe(conn, user_id):
                return no_activo
            resultado = await TotpService(conn).verify(
                user_id, tenant_slug, pedido.code, contexto=pedido.contexto
            )
        except asyncpg.UndefinedTableError:
            logger.warning("verify_totp: tenant '%s' sin aprovisionar; no_activo", tenant_slug)
            return no_activo
    if resultado.valido:
        # Dice DE QUIÉN era el factor verificado (AREA-04-05).
        resultado = resultado.model_copy(update={"user_id": user_id})
    return resultado.model_dump(mode="json")


async def signing_key_status(*, tenant_slug: str, user_id: uuid.UUID) -> dict[str, Any]:
    """Estado de la clave personal de la persona; nunca la clave privada.

    Decide entre `xades_personal` (estado `activo`) y la firma nativa.
    """
    async with _tenant_conn(tenant_slug) as conn:
        if not await _existe(conn, user_id):
            raise AuthUnavailableError("el usuario no está aprovisionado en esta entidad")
        estado = await SigningKeyService(conn).get_status(user_id)
    return estado.model_copy(update={"user_id": user_id}).model_dump(mode="json")


async def sign_digest(
    *,
    tenant_slug: str,
    user_id: uuid.UUID,
    digest: bytes,
    totp_code: str,
    cert_fingerprint: str,
) -> dict[str, Any]:
    """Firma remota con la clave PERSONAL de la persona (E17/F4).

    `digest` es el SHA-256 de `C14N-exclusiva(ds:SignedInfo)`, nunca el
    documento; la clave privada no sale de auth. El TOTP se verifica-y-consume
    en la MISMA transacción que la firma. `cert_fingerprint` es el del
    certificado que el llamante incrustó en `ds:KeyInfo`: si difiere del
    vigente, 409 `certificado_cambiado` y el factor queda intacto.
    """
    try:
        pedido = InternalSignDigestRequest(
            digest_b64=b64encode(digest).decode("ascii"),
            totp_code=totp_code,
            cert_fingerprint=cert_fingerprint,
        )
    except ValidationError as exc:
        raise SignDigestError(
            422, {"error": "pedido_invalido", "detail": str(exc), "status": 422}
        ) from exc
    async with _tenant_conn(tenant_slug) as conn:
        if not await _existe(conn, user_id):
            raise SignDigestError(
                422,
                {
                    "error": "signing_key_no_activa",
                    "detail": "El usuario no está aprovisionado en esta entidad",
                    "status": 422,
                },
            )
        try:
            (
                signature_der,
                cert_pem,
                fingerprint,
                serial,
                not_after,
                ca_cert_pem,
                ca_fingerprint,
            ) = await SigningKeyService(conn).load_and_sign_digest(
                user_id,
                tenant_slug,
                digest,
                pedido.totp_code,
                cert_fingerprint=pedido.cert_fingerprint,
            )
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise AuthUnavailableError(f"sign-digest -> {exc.status_code}") from exc
            detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
            raise SignDigestError(exc.status_code, detail) from exc
    return InternalSignDigestResponse(
        signature_value_b64=b64encode(signature_der).decode("ascii"),
        certificado_pem=cert_pem,
        certificado_fingerprint=fingerprint,
        serial=serial,
        not_after=not_after,
        ca_cert_pem=ca_cert_pem,
        ca_fingerprint=ca_fingerprint,
    ).model_dump(mode="json")


async def signing_key_revocation_status(*, tenant_slug: str, fingerprint: str) -> dict[str, Any]:
    """Estado de revocación de un certificado LEAF por su huella, en este tenant.

    Una huella de OTRO tenant nunca resuelve `vigente` (fail-closed:
    `desconocido`). Solo `{status, revocado_at, motivo_revocacion}`.
    """
    async with _tenant_conn(tenant_slug) as conn:
        estado = await SigningKeyService(conn).revocation_status(fingerprint)
    return estado.model_dump(mode="json")


async def service_token(conn: asyncpg.Connection, *, tenant_slug: str, audience: str) -> tuple[str, int]:
    """Token de la cuenta de servicio de la entidad para `audience` (ADR-048 §3).

    Lo firma el propio proceso: no hay secreto que abrir ni emisor externo al
    que pedírselo. `(token, segundos de vida)`. Sin cuenta activa, `AuthUnavailableError`.
    """
    from auth.repositories.identity_repository import IdentityRepository
    from auth.services.auth_service import emitir_token_servicio

    cuenta = await IdentityRepository(conn).service_account_for(tenant_slug, audience)
    if cuenta is None:
        raise AuthUnavailableError("sin cuenta de servicio")
    respuesta = await emitir_token_servicio(conn, cuenta)
    return respuesta.access_token, respuesta.expires_in
