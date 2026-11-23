# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Firma electrónica PERSONAL PKI por-usuario (E17/F4). Dos routers:

- ``router`` (``/api/v1/auth/me/signing-key``): identidad resuelta por
  ``get_caller`` (``UsuarioActualMiddleware``).
- ``admin_router``: revocación administrativa (``USUA_PERM_ADMIN``).
- La carga del certificado emitido OFFLINE no es una ruta: la hace el operador
  con ``python -m auth.ops.activar_certificado`` (ADR-047, sin tokens internos).

Estado, firma remota y revocación, que signature consulta al firmar y al
verificar, no son rutas: son ``auth.api`` (ADR-047 §1).
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from orpycamcp_common.database import get_tenant_conn

from auth.core.authz import Caller, get_caller, require_permission
from auth.core.security import validate_token
from auth.schemas.signing_key import (
    AdminSigningKeyRevokeRequest,
    SigningKeyEnrollRequest,
    SigningKeyEnrollResponse,
    SigningKeyRevokeRequest,
    SigningKeyRevokeResponse,
    SigningKeyStatusResponse,
)
from auth.services.signing_key_service import SigningKeyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth/me/signing-key", tags=["signing-key"])
# Admin: gateado por USUA_PERM_ADMIN a nivel de router (E17/F4, Incremento 2)
# — DISTINTO de `router` (identidad SIEMPRE propia, get_caller) y de
# `internal_router` (X-Internal-Token, service-to-service). Prefijo separado
# de `app.routers.rbac.router` (que también cubre `/api/v1/auth`) para
# mantener el ciclo de vida de la firma personal en este módulo.
admin_router = APIRouter(
    prefix="/api/v1/auth/admin/users",
    tags=["signing-key-admin"],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)

_bearer_scheme = HTTPBearer(auto_error=False)


async def _resolve_tenant_slug_for_aad(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    """Misma resolución que ``app.routers.totp._resolve_tenant_slug_for_aad``
    — el ``tenant_slug`` atado al AAD de AESGCM viene del CLAIM del propio
    bearer del llamante, con el header ``X-Tenant-Slug`` como único fallback
    (bearer indecodificable — nunca convierte una request ya autenticada por
    el gateway en un fallo duro)."""
    header_slug = request.headers.get("X-Tenant-Slug", "")
    if credentials is None:
        return header_slug
    try:
        claims = await validate_token(credentials.credentials)
    except PyJWTError:
        return header_slug
    return claims.get("tenant_slug") or header_slug


# ---------------------------------------------------------------------------
# Public — /api/v1/auth/me/signing-key/*
# ---------------------------------------------------------------------------


@router.post(
    "/enroll",
    response_model=SigningKeyEnrollResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll (or re-enroll) a personal PKI signing key — returns the CSR, never the private key",
    # S2: enrolar una credencial de firma exige el permiso de firmar —
    # coherente con que solo los firmantes tienen clave. Sin este gate,
    # cualquier llamante autenticado (identidad siempre propia, get_caller)
    # podía re-enrolar y auto-provocarse un self-DoS: un re-enroll revoca
    # atómicamente la clave `activo` previa (deja `pendiente_emision`,
    # inútil hasta reemisión offline) aunque el llamante no tenga permiso
    # de firma alguno. NO se toca ese comportamiento de revocación (ver
    # docstring de `SigningKeyService.enroll`) — exigir step-up (TOTP) para
    # revocar una clave `activo` al re-enrolar queda como endurecimiento
    # de Inc.2.
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def enroll_signing_key(
    payload: SigningKeyEnrollRequest,
    request: Request,
    response: Response,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> SigningKeyEnrollResponse:
    tenant_slug = await _resolve_tenant_slug_for_aad(request, credentials)
    result = await SigningKeyService(conn).enroll(
        caller.user_id, tenant_slug, payload.step_up_totp_code
    )
    # No hay secreto en el body (el CSR es público), pero se mantiene la
    # misma disciplina que /me/totp/enroll por defensa en profundidad.
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "",
    response_model=SigningKeyStatusResponse,
    summary="Current caller's signing-key status — NEVER returns the private key",
)
async def get_signing_key_status(
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SigningKeyStatusResponse:
    return await SigningKeyService(conn).get_status(caller.user_id)


@router.delete(
    "",
    response_model=SigningKeyRevokeResponse,
    summary="Self-revoke the caller's signing-key credential (step-up TOTP always required)",
)
async def revoke_signing_key(
    payload: SigningKeyRevokeRequest,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> SigningKeyRevokeResponse:
    """E17/F4, Incremento 2 (A2 — revocación por estado en BD, sin CRL
    offline). ``ca.key`` de ninguna CA se toca aquí: revocar es un simple
    cambio de ``estado`` + asiento, verificado por
    ``GET /internal/signing-key/revocation-status``."""
    tenant_slug = await _resolve_tenant_slug_for_aad(request, credentials)
    await SigningKeyService(conn).revoke_self(
        caller.user_id, tenant_slug, payload.step_up_totp_code, motivo=payload.motivo
    )
    return SigningKeyRevokeResponse(estado="revocado")


# ---------------------------------------------------------------------------
# Admin — /api/v1/auth/admin/users/{user_id}/signing-key/* (USUA_PERM_ADMIN)
# ---------------------------------------------------------------------------


@admin_router.post(
    "/{user_id}/signing-key/revoke",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
    response_model=SigningKeyRevokeResponse,
    summary="Admin-revoke another user's signing-key credential (USUA_PERM_ADMIN)",
)
async def admin_revoke_signing_key(
    user_id: uuid.UUID,
    payload: AdminSigningKeyRevokeRequest,
    request: Request,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SigningKeyRevokeResponse:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    await SigningKeyService(conn).revoke_admin(
        user_id, tenant_slug, caller.user_id, motivo=payload.motivo
    )
    return SigningKeyRevokeResponse(estado="revocado")
