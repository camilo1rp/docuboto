# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import hashlib
import json
import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.client_ip import resolve_client_ip

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canal derivation — server-side only (D-03)
# ---------------------------------------------------------------------------
# The audit `canal` field identifies the request origin in the chain-of-custody
# log.  It MUST be derived here, at the gateway trust boundary, and never taken
# from client-supplied headers (X-Canal is absent from FORWARDED_REQUEST_HEADERS
# in proxy.py, so it is already stripped before reaching upstream services).
#
# Current mapping (path-based):
#   /api/v1/mcp/…  → 'mcp'  (MCP-server namespace, ADR-019)
#   everything else → 'api'
#
# 'ui' is reserved for SvelteKit frontend requests.  It will be set when the
# nginx reverse-proxy injects a trusted origin marker that the gateway can
# verify cryptographically (independent of the client-accessible User-Agent).

_MCP_PREFIX = "/api/v1/mcp/"


def derive_canal(path: str) -> str:
    """Returns the audit canal for *path*, derived entirely server-side.

    Never call this with a value taken from a client header.
    """
    if path.startswith(_MCP_PREFIX):
        return "mcp"
    return "api"


PUBLIC_PATHS = {
    "/api/v1/auth/token",
    "/api/v1/auth/refresh",
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/api/v1/redoc",
    "/health",
    # Disponibilidad: la consulta el balanceador, que no tiene credenciales.
    # No publica detalle interno — el estado de cada servicio es una palabra y
    # el motivo del fallo solo va al log (ver `routers/health.py`).
    "/health/ready",
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
                    "detail": "Falta la cabecera Authorization con el token Bearer",
                    "status": 401,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        http_client: httpx.AsyncClient = request.app.state.http_client
        auth_service_url: str = request.app.state.auth_service_url

        # Caché de la validación (Redis). El gateway llamaba a auth-service en
        # TODA petición no pública, lo que lo convertía en un punto único de fallo
        # síncrono —y en el cuello de botella del sistema— pese a no tener
        # réplicas, mientras gateway, document y storage sí las tienen.
        #
        # El TTL es CORTO y además se acota al `exp` del propio token: cachear más
        # allá de su caducidad convertiría un token expirado en uno vivo. No hay
        # invalidación por revocación —un token revocado sigue valiendo lo que dure
        # el TTL—, y por eso el TTL es de segundos y no de minutos: es el precio
        # explícito de esta caché, no un olvido.
        cache_key = _validation_key(authorization)
        redis = getattr(request.app.state, "redis", None)
        payload = None
        if redis is not None:
            try:
                crudo = await redis.get(cache_key)
                if crudo:
                    payload = json.loads(crudo)
            except Exception:  # noqa: BLE001 — la caché nunca decide el acceso
                logger.debug("Fallo leyendo la caché de validación", exc_info=True)

        if payload is not None:
            return await _continuar_con_identidad(request, call_next, payload)

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
                    "detail": "No se pudo contactar el servicio de autenticación",
                    "status": 503,
                },
            )

        if validate_resp.status_code != 200:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "auth_service_error",
                    "detail": "Respuesta inesperada del servicio de autenticación",
                    "status": 503,
                },
            )

        payload = validate_resp.json()

        # Solo se cachea el SÍ. Un `valid: false` cacheado ahorraría poco y
        # retrasaría el momento en que un token recién emitido empieza a funcionar.
        if redis is not None and payload.get("valid", False):
            try:
                await redis.set(
                    cache_key,
                    json.dumps(payload),
                    ex=_ttl_validacion(payload, request.app.state.auth_cache_ttl),
                )
            except Exception:  # noqa: BLE001 — la caché nunca decide el acceso
                logger.debug("Fallo escribiendo la caché de validación", exc_info=True)

        if not payload.get("valid", False):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "El token es inválido o expiró",
                    "status": 401,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Inject validated identity headers so downstream services trust them
        # without needing to re-validate the JWT themselves.
        # X-Canal is derived server-side here; the client-supplied value (if any)
        # is already stripped by the FORWARDED_REQUEST_HEADERS whitelist in proxy.py.
        return await _continuar_con_identidad(request, call_next, payload)


def _validation_key(authorization: str) -> str:
    """Clave de caché derivada del token, sin guardar el token.

    Se cachea su SHA-256: Redis no necesita el token en claro para nada, y un
    volcado de Redis no debe entregar credenciales vivas.
    """
    huella = hashlib.sha256(authorization.encode()).hexdigest()
    return f"authvalidate:{huella}"


def _ttl_validacion(payload: dict, ttl_maximo: int) -> int:
    """TTL acotado por el `exp` del token: nunca sobrevive a su caducidad."""
    restante = payload.get("expires_in")
    if isinstance(restante, int) and restante > 0:
        return max(1, min(ttl_maximo, restante))
    return ttl_maximo


async def _continuar_con_identidad(request: Request, call_next, payload: dict) -> Response:
    """Inyecta la identidad validada y sigue la cadena.

    Extraído para que el camino con caché y el camino sin ella construyan las
    cabeceras en UN solo sitio: dos copias divergen, y la que se olvide de añadir
    una cabecera nueva fallará solo cuando la caché esté caliente — es decir, en
    producción y no en las pruebas.
    """
    roles = payload.get("roles", [])
    request.state.auth_headers = {
        "X-User-Id": payload.get("user_id", ""),
        "X-Username": payload.get("username", ""),
        "X-Tenant-Slug": payload.get("tenant_slug") or "",
        "X-User-Roles": ",".join(roles),
        "X-Canal": derive_canal(request.url.path),
        # IP derivada AQUÍ por saltos de confianza (D-03, mismo criterio que
        # X-Canal): los servicios la sellan en `audit_log`, un registro inmutable
        # con valor probatorio. `x-real-ip` NO está en FORWARDED_REQUEST_HEADERS,
        # así que un cliente no puede forjarla; antes, los servicios leían el
        # `X-Forwarded-For` crudo, que sí reenvía el gateway y sí escribe el
        # cliente. Se recalcula por petición aunque la validación venga de caché:
        # la identidad de red es de la PETICIÓN, no del token.
        "X-Real-Ip": resolve_client_ip(
            request, getattr(request.app.state, "trusted_proxies", "")
        ),
    }

    return await call_next(request)
