# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Firma electrónica PERSONAL PKI por-usuario (E17/F4, Incremento 1). Tres
routers con visibilidad MUY distinta — mismo patrón que ``app/routers/totp.py``:

- ``router`` (``/api/v1/auth/me/signing-key``): PÚBLICO vía api-gateway.
  Identidad resuelta por ``get_caller`` (``X-User-Id`` inyectado por el
  gateway tras ``/validate``).

- ``internal_router`` (``/internal/signing-key``): NUNCA expuesto por el
  gateway (fuera de ``/api/v1``, igual que ``/internal/totp``). Tres rutas:

    - ``GET /status``: SOLO signature-service, para decidir si un acto de
      ``CadenaFirmaService.firmar()`` toma el camino ``xades_personal`` (cert
      ``activo``) o cae a la firma nativa HMAC — mismo doble gate que
      ``sign-digest`` (identidad del Bearer + ``X-Internal-Token``). Existe
      porque el router PÚBLICO (``GET /me/signing-key``) depende de
      ``X-User-Id`` (lo inyecta el gateway tras ``/validate``), que
      signature-service NUNCA tiene al llamar DIRECTO a auth-service (Q3) —
      aquí la identidad se deriva del propio Bearer, igual que ``sign-digest``.
    - ``POST /sign-digest``: SOLO signature-service, servicio-a-servicio,
      reenviando el ``Authorization`` del firmante (D1: identidad SIEMPRE
      del Bearer, NUNCA de un ``user_id`` en el body — cierra IDOR) MÁS
      ``X-Internal-Token`` (demuestra que el llamador es signature-service).
      Ambos gates son independientes y ninguno sustituye al otro.
    - ``POST /activate``: operación administrativa/de sistema (carga el
      certificado emitido OFFLINE) — SOLO ``X-Internal-Token``, sin Bearer.
"""

from __future__ import annotations

import logging
import uuid
from base64 import b64decode, b64encode

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError

from app.core.authz import Caller, get_caller, require_permission
from app.core.database import get_db_conn, get_tenant_conn, tenant_schema
from app.core.internal_auth import require_internal_token
from app.core.security import validate_token
from app.repositories.rbac_repository import RBACRepository
from app.schemas.signing_key import (
    InternalActivateSigningKeyRequest,
    InternalSignDigestRequest,
    InternalSignDigestResponse,
    SigningKeyEnrollResponse,
    SigningKeyStatusResponse,
)
from app.services.signing_key_service import SigningKeyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth/me/signing-key", tags=["signing-key"])
internal_router = APIRouter(prefix="/internal/signing-key", tags=["signing-key-internal"])

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


async def _resolve_bearer_user(
    credentials: HTTPAuthorizationCredentials | None,
    conn: asyncpg.Connection,
) -> tuple[uuid.UUID, str]:
    """Resuelve ``(user_id, tenant_slug)`` a partir del ``Authorization``
    reenviado — MISMA lógica en ``sign-digest`` y ``status`` (D1: identidad
    SIEMPRE del Bearer, nunca de un campo de body/query). Deja el
    ``search_path`` fijado al schema del tenant al retornar; el caller es
    responsable de restaurarlo a ``public`` en un ``finally``.
    """
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "signing_key_no_activa",
                "detail": "Caller has no tenant context",
                "status": 422,
            },
        )

    schema = tenant_schema(tenant_slug)  # 400 si el claim está mal formado
    await conn.execute(f'SET search_path = "{schema}", public')
    rbac = RBACRepository(conn)
    user = await rbac.get_user_by_keycloak_sub(claims["user_id"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "signing_key_no_activa",
                "detail": "Caller is not provisioned in this tenant",
                "status": 422,
            },
        )
    return user["id"], tenant_slug


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
    request: Request,
    response: Response,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> SigningKeyEnrollResponse:
    tenant_slug = await _resolve_tenant_slug_for_aad(request, credentials)
    result = await SigningKeyService(conn).enroll(caller.user_id, tenant_slug)
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


# ---------------------------------------------------------------------------
# Internal — /internal/signing-key/* (service-to-service only, NOT gateway-routed)
# ---------------------------------------------------------------------------


@internal_router.post(
    "/sign-digest",
    response_model=InternalSignDigestResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Verify-and-consume a TOTP code and sign a pre-computed digest for the bearer's own identity",
)
async def internal_sign_digest(
    payload: InternalSignDigestRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> InternalSignDigestResponse:
    """Firma remota (E17/F4, Incremento 1) — SOLO signature-service la llama.

    Doble gate: ``X-Internal-Token`` (``require_internal_token``, el
    llamador es signature-service) Y la identidad del firmante derivada del
    ``Authorization`` reenviado (D1 — NUNCA de un ``user_id`` en el body,
    cierra IDOR), exactamente como ``POST /internal/totp/verify``.
    """
    try:
        user_id, tenant_slug = await _resolve_bearer_user(credentials, conn)
        try:
            digest = b64decode(payload.digest_b64, validate=True)
        except Exception:  # noqa: BLE001 — base64 corrupto, 422 explícito
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "digest_invalido", "detail": "digest_b64 is not valid base64", "status": 422},
            ) from None

        signature_der, cert_pem, fingerprint, serial, not_after = await SigningKeyService(
            conn
        ).load_and_sign_digest(user_id, tenant_slug, digest, payload.totp_code)

        return InternalSignDigestResponse(
            signature_value_b64=b64encode(signature_der).decode("ascii"),
            certificado_pem=cert_pem,
            certificado_fingerprint=fingerprint,
            serial=serial,
            not_after=not_after,
        )
    finally:
        try:
            await conn.execute("SET search_path = public")
        except Exception:
            pass


@internal_router.get(
    "/status",
    response_model=SigningKeyStatusResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Signing-key status for the bearer's own identity (signature-service only)",
)
async def internal_signing_key_status(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> SigningKeyStatusResponse:
    """Consultado por ``CadenaFirmaService.firmar()`` (signature-service) para
    decidir entre el camino ``xades_personal`` (cert ``activo``) y el
    fallback nativo HMAC — ver el docstring de módulo. Doble gate igual que
    ``sign-digest``; nunca devuelve la clave privada."""
    try:
        user_id, _tenant_slug = await _resolve_bearer_user(credentials, conn)
        return await SigningKeyService(conn).get_status(user_id)
    finally:
        try:
            await conn.execute("SET search_path = public")
        except Exception:
            pass


@internal_router.post(
    "/activate",
    response_model=SigningKeyStatusResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Load an offline-issued certificate onto a pending signing-key credential",
)
async def internal_activate_signing_key(
    payload: InternalActivateSigningKeyRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> SigningKeyStatusResponse:
    """Carga del certificado emitido OFFLINE (``scripts/gen-user-cert.sh``).

    Operación administrativa/de sistema — SOLO gate ``X-Internal-Token``
    (sin Bearer de usuario: el operador que firmó el CSR offline ya validó
    la identidad fuera de banda antes de invocar esta ruta).
    """
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    schema = tenant_schema(tenant_slug)
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        return await SigningKeyService(conn).activar_certificado(
            payload.user_id, payload.cert_pem, tenant_slug
        )
    finally:
        try:
            await conn.execute("SET search_path = public")
        except Exception:
            pass
