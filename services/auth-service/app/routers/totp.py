# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
TOTP (E06, RFC 6238) — segundo factor para la firma personal.

Dos routers con visibilidad MUY distinta:

- ``router`` (``/api/v1/auth/me/totp``): PÚBLICO vía api-gateway (el prefijo
  ``/api/v1/auth/`` ya está enrutado en ``proxy.py``). Identidad resuelta por
  ``get_caller`` (``X-User-Id`` inyectado por el gateway tras ``/validate``) —
  mismo patrón que ``clearance.py``/``context.py``.

- ``internal_router`` (``/internal/totp``): NUNCA expuesto por el gateway (su
  único route matcher es ``/api/v1/{path:path}`` — cualquier ruta fuera de
  ``/api/v1`` es, por construcción, inalcanzable vía gateway, igual que
  ``/internal/*`` en otros servicios). Solo signature-service lo llama,
  directo por la red Docker, reenviando el ``Authorization`` del firmante
  (D1: la identidad se deriva del bearer, NUNCA de un ``user_id`` de body —
  cierra IDOR). La identidad se resuelve aquí mismo vía ``validate_token`` +
  ``get_user_by_keycloak_sub``, exactamente como hace
  ``AuthService.validate()`` para ``/api/v1/auth/validate``.
"""

from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError

from app.core.authz import Caller, get_caller
from app.core.database import get_db_conn, get_tenant_conn, tenant_schema
from app.core.security import validate_token
from app.repositories.rbac_repository import RBACRepository
from app.schemas.totp import (
    InternalTotpVerifyRequest,
    InternalTotpVerifyResponse,
    TotpActivateRequest,
    TotpActivateResponse,
    TotpEnrollRequest,
    TotpEnrollResponse,
    TotpRecoveryRegenerateRequest,
    TotpRecoveryRegenerateResponse,
    TotpRevokeRequest,
    TotpRevokeResponse,
    TotpStatusResponse,
)
from app.services.totp_service import TotpService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth/me/totp", tags=["totp"])
internal_router = APIRouter(prefix="/internal/totp", tags=["totp-internal"])

_bearer_scheme = HTTPBearer(auto_error=False)


async def _resolve_tenant_slug_for_aad(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    """Resolves the ``tenant_slug`` bound into the AESGCM AAD (``build_aad``)
    from the caller's OWN bearer JWT claim — the same source
    ``/internal/totp/verify`` uses — instead of the ``X-Tenant-Slug`` header.

    In normal operation the header and the claim agree (the gateway derives
    the header from this very claim via its own call to ``/validate``), but
    binding the AAD to the header made it a second, independent source of
    truth: if header and claim ever diverged (misconfig), a code minted here
    would fail to decrypt in ``verify``. Falls back to the header only if the
    bearer can't be decoded (e.g. transient JWKS issue) so this never turns
    an otherwise-authenticated request (already vetted by the gateway) into
    a hard failure — it only removes the header as an alternate AAD source.
    """
    header_slug = request.headers.get("X-Tenant-Slug", "")
    if credentials is None:
        return header_slug
    try:
        claims = await validate_token(credentials.credentials)
    except PyJWTError:
        return header_slug
    return claims.get("tenant_slug") or header_slug


# ---------------------------------------------------------------------------
# Public — /api/v1/auth/me/totp/*
# ---------------------------------------------------------------------------


@router.post(
    "/enroll",
    response_model=TotpEnrollResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll (or re-enroll) a TOTP credential — reveals the secret exactly once",
)
async def enroll_totp(
    payload: TotpEnrollRequest,
    request: Request,
    response: Response,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TotpEnrollResponse:
    tenant_slug = await _resolve_tenant_slug_for_aad(request, credentials)
    result = await TotpService(conn).enroll(
        caller.user_id,
        tenant_slug,
        payload.step_up_totp_code,
        payload.step_up_recovery_code,
    )
    # The secret is embedded in otpauth_uri — never allow a cache/proxy to store it.
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/activate",
    response_model=TotpActivateResponse,
    summary="Activate a pending TOTP enrollment with the first generated code",
)
async def activate_totp(
    payload: TotpActivateRequest,
    request: Request,
    response: Response,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TotpActivateResponse:
    tenant_slug = await _resolve_tenant_slug_for_aad(request, credentials)
    result = await TotpService(conn).activate(caller.user_id, tenant_slug, payload.code)
    # La respuesta lleva los códigos de recuperación en claro — la única vez
    # que existen fuera de la memoria del usuario. Mismo `no-store` que el
    # `otpauth://` de enroll: ninguna caché ni proxy debe conservarlos.
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "",
    response_model=TotpStatusResponse,
    summary="Current caller's TOTP status — NEVER returns the secret",
)
async def get_totp_status(
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> TotpStatusResponse:
    return await TotpService(conn).status(caller.user_id)


@router.delete(
    "",
    response_model=TotpRevokeResponse,
    summary="Revoke the caller's TOTP credential (step-up required if active)",
)
async def revoke_totp(
    payload: TotpRevokeRequest,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TotpRevokeResponse:
    tenant_slug = await _resolve_tenant_slug_for_aad(request, credentials)
    await TotpService(conn).revoke_self(
        caller.user_id,
        tenant_slug,
        payload.step_up_totp_code,
        payload.step_up_recovery_code,
    )
    return TotpRevokeResponse(estado="revocado")


@router.post(
    "/recovery-codes",
    response_model=TotpRecoveryRegenerateResponse,
    summary="Regenerate the caller's recovery codes — reveals them exactly once",
)
async def regenerate_recovery_codes(
    payload: TotpRecoveryRegenerateRequest,
    request: Request,
    response: Response,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TotpRecoveryRegenerateResponse:
    tenant_slug = await _resolve_tenant_slug_for_aad(request, credentials)
    result = await TotpService(conn).regenerate_recovery_codes(
        caller.user_id,
        tenant_slug,
        payload.step_up_totp_code,
        payload.step_up_recovery_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


# ---------------------------------------------------------------------------
# Internal — /internal/totp/* (service-to-service only, NOT gateway-routed)
# ---------------------------------------------------------------------------


@internal_router.post(
    "/verify",
    response_model=InternalTotpVerifyResponse,
    summary="Verify-and-consume a TOTP code for the bearer's own identity (signature-service only)",
)
async def internal_verify_totp(
    payload: InternalTotpVerifyRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> InternalTotpVerifyResponse:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token", "detail": "Authorization header required", "status": 401},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = await validate_token(credentials.credentials)
    except PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "detail": "Bearer token is invalid or expired", "status": 401},
        )

    tenant_slug = claims.get("tenant_slug")
    if not tenant_slug:
        # No tenant context (e.g. superadmin) — auth_user_totp is tenant-scoped,
        # so there is nothing to resolve. Fail-closed, never 500.
        return InternalTotpVerifyResponse(valido=False, motivo="no_activo")

    schema = tenant_schema(tenant_slug)  # 400 if the claim itself is malformed
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        rbac = RBACRepository(conn)
        user = await rbac.get_user_by_keycloak_sub(claims["user_id"])
        if user is None:
            return InternalTotpVerifyResponse(valido=False, motivo="no_activo")
        return await TotpService(conn).verify(
            user["id"], tenant_slug, payload.code, contexto=payload.contexto
        )
    except asyncpg.UndefinedTableError:
        # Tenant not provisioned yet — same fail-closed criterion as AuthService.validate().
        logger.warning(
            "internal_verify_totp: tenant '%s' not provisioned; treating as no_activo",
            tenant_slug,
        )
        return InternalTotpVerifyResponse(valido=False, motivo="no_activo")
    finally:
        try:
            await conn.execute("SET search_path = public")
        except Exception:
            pass
