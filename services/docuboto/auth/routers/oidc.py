# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El emisor OIDC que consumen las máquinas (ADR-048 §1, §3).

Descubrimiento, JWKS y `client_credentials`. Es lo que el proveedor PPI declara
como `issuer` + `jwks_uri`; no hay flujos de usuario aquí (las personas entran
por `/api/v1/auth/login`).
"""

from __future__ import annotations

import base64
import binascii
from typing import Any
from urllib.parse import unquote

import asyncpg
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from orpycamcp_common.database import get_conn as get_db_conn

from auth.core import security
from auth.repositories.audit_repository import AuditRepository
from auth.services.auth_service import AuthError, AuthService
from docuboto.config import auth as settings

router = APIRouter(tags=["oidc"])

_SIN_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _oauth_error(status: int, error: str) -> JSONResponse:
    """Errores con la forma de RFC 6749 §5.2, que es la que espera un cliente OAuth."""
    headers = dict(_SIN_CACHE)
    if status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="docuboto"'
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


def _basic(request: Request) -> tuple[str, str] | None:
    cabecera = request.headers.get("authorization") or ""
    if not cabecera.lower().startswith("basic "):
        return None
    try:
        crudo = base64.b64decode(cabecera.split(" ", 1)[1].strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    cid, sep, secreto = crudo.partition(":")
    if not sep:
        return None
    return unquote(cid), unquote(secreto)


@router.get("/.well-known/openid-configuration", include_in_schema=False)
async def openid_configuration() -> dict[str, Any]:
    base = settings.identity_issuer.rstrip("/")
    return {
        "issuer": settings.identity_issuer,
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "token_endpoint": f"{base}/oauth/token",
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "response_types_supported": ["token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": [security.ALG],
        "claims_supported": ["iss", "sub", "aud", "exp", "iat", "jti", "client_id", "azp",
                             "tenant_slug", "scope", "principal_type"],
    }


@router.get("/.well-known/jwks.json", include_in_schema=False)
async def jwks(conn: asyncpg.Connection = Depends(get_db_conn)) -> JSONResponse:
    return JSONResponse(
        content=await security.jwks(conn),
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.post("/oauth/token", include_in_schema=False)
async def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    if grant_type != "client_credentials":
        return _oauth_error(400, "unsupported_grant_type")
    basic = _basic(request)
    if basic is not None:
        client_id, client_secret = basic
    if not client_id or not client_secret:
        return _oauth_error(401, "invalid_client")
    try:
        token = await AuthService().client_credentials(conn, client_id, client_secret)
    except AuthError as exc:
        await AuditRepository(conn).log("client_credentials_failure", detail=exc.code)
        if exc.code == "invalid_client":
            return _oauth_error(401, "invalid_client")
        return _oauth_error(400, "invalid_grant")
    return JSONResponse(
        content={
            "access_token": token.access_token,
            "token_type": "Bearer",
            "expires_in": token.expires_in,
        },
        headers=_SIN_CACHE,
    )
