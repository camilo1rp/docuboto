# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

PUBLIC_PATHS = {
    "/api/v1/auth/token",
    "/api/v1/auth/refresh",
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/api/v1/redoc",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    # FastAPI mounts docs under /docs with sub-paths like /docs/oauth2-redirect
    if path.startswith("/docs/"):
        return True
    # Consulta pública por código de verificación (E13) — sin login.
    if path.startswith("/api/v1/public/"):
        return True
    return False


class JWTValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if _is_public(request.url.path):
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "Authorization Bearer token required",
                    "status": 401,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        http_client: httpx.AsyncClient = request.app.state.http_client
        auth_service_url: str = request.app.state.auth_service_url

        try:
            validate_resp = await http_client.get(
                f"{auth_service_url}/api/v1/auth/validate",
                headers={"Authorization": authorization},
            )
        except httpx.RequestError:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "auth_service_unavailable",
                    "detail": "Authentication service is not reachable",
                    "status": 503,
                },
            )

        if validate_resp.status_code != 200:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "auth_service_error",
                    "detail": "Unexpected response from authentication service",
                    "status": 503,
                },
            )

        payload = validate_resp.json()

        if not payload.get("valid", False):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "Invalid or expired token",
                    "status": 401,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Inject validated identity headers so downstream services trust them
        # without needing to re-validate the JWT themselves.
        roles = payload.get("roles", [])
        request.state.auth_headers = {
            "X-User-Id": payload.get("user_id", ""),
            "X-Username": payload.get("username", ""),
            "X-Tenant-Slug": payload.get("tenant_slug") or "",
            "X-User-Roles": ",".join(roles),
        }

        return await call_next(request)
