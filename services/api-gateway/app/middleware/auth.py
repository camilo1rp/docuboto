# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import hashlib
import hmac
import json
import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from orpycamcp_common import identidad
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
# Mapping:
#   /api/v1/mcp/…  → 'mcp'  (MCP-server namespace, ADR-019)
#   a call carrying a VALID origin mark (`X-Origen-Canal`, signed by the mcp-server
#   with the shared identity secret and bound to tenant + token, AREA-11-04) → 'mcp'
#   everything else → 'api'
#
# The mark exists because everything the mcp-server does on behalf of a person
# (the assistant confirming a radicado, a transfer) reaches the gateway through
# `/api/v1/<domain>` and used to be recorded as 'api', indistinguishable from a
# direct call. A client without the signing secret cannot forge it; with a bad or
# expired signature the canal falls back to 'api' (the safe side).
#
# 'ui' is reserved for SvelteKit frontend requests.  It will be set when the
# nginx reverse-proxy injects a trusted origin marker that the gateway can
# verify cryptographically (independent of the client-accessible User-Agent).

_MCP_PREFIX = "/api/v1/mcp/"


def derive_canal(
    path: str,
    origen: str | None = None,
    *,
    tenant: str = "",
    authorization: str = "",
) -> str:
    """Returns the audit canal for *path*, derived entirely server-side.

    *origen* is the raw `X-Origen-Canal` header value; it only counts when its
    signature verifies for this *tenant* and *authorization*. *tenant* is the one of
    the validated token, never a client-supplied header. Never call this with any
    other client-supplied header.
    """
    if path.startswith(_MCP_PREFIX):
        return "mcp"
    if (
        identidad.verificar_origen(origen, tenant=tenant, authorization=authorization)
        == "mcp"
    ):
        return "mcp"
    return "api"


#: Rutas del CATÁLOGO de la API. No están en `PUBLIC_PATHS` porque su
#: publicidad no es incondicional: depende de `openapi_publico`, que en
#: producción es falso. Describen la superficie entera —los siete servicios que
#: se agregan, sus esquemas y los endpoints internos que el ruteo retira a
#: propósito—, así que servirlas sin autenticar es entregar el mapa que un
#: atacante tendría que levantar a mano.
_RUTAS_OPENAPI = frozenset(
    {
        "/api/v1/openapi.json",
        "/api/v1/docs",
        "/api/v1/redoc",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)

PUBLIC_PATHS = {
    "/api/v1/auth/token",
    "/api/v1/auth/refresh",
    "/health",
    # Disponibilidad: la consulta el balanceador, que no tiene credenciales.
    # No publica detalle interno — el estado de cada servicio es una palabra y
    # el motivo del fallo solo va al log (ver `routers/health.py`).
    "/health/ready",
}


def _is_public(path: str, *, openapi_publico: bool = False) -> bool:
    """¿Se atiende esta ruta sin credenciales?

    `openapi_publico` NO tiene un valor por omisión permisivo a propósito: un
    default que significa «abierto» es la forma en que estas listas se aflojan
    sin que nadie lo decida.
    """
    if path in PUBLIC_PATHS:
        return True
    # El catálogo solo es público si el despliegue lo declara.
    if path in _RUTAS_OPENAPI or path.startswith("/docs/"):
        return openapi_publico
    # Consulta pública por código de verificación (E13) — sin login.
    if path.startswith("/api/v1/public/"):
        return True
    return False


class JWTValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if _is_public(
            request.url.path,
            openapi_publico=bool(getattr(request.app.state, "openapi_publico", False)),
        ):
            # Ruta pública: no hay persona que firmar, pero la IP del cliente
            # (por saltos de confianza, NUNCA el X-Forwarded-For crudo) viaja
            # firmada y ligada a la ruta para la constancia de autorización
            # (Ley 1581, ADR-031). `x-real-ip` no está en la whitelist de
            # reenvío, así que lo que el cliente mande se sobrescribe.
            request.state.auth_headers = identidad.firmar_ip_publica(
                resolve_client_ip(request, getattr(request.app.state, "trusted_proxies", "")),
                request.url.path,
            )
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
        # Sin secreto MAC la caché se APAGA (fail-closed): una entrada sin
        # integridad es la que cualquiera con la contraseña de Redis podría forjar.
        mac_secret: str = getattr(request.app.state, "auth_cache_mac_secret", "") or ""
        if not mac_secret:
            redis = None
        payload = None
        if redis is not None:
            try:
                crudo = await redis.get(cache_key)
                if crudo:
                    payload = _leer_entrada_cache(crudo, cache_key, mac_secret)
            except Exception:  # noqa: BLE001 — la caché nunca decide el acceso
                logger.debug("Fallo leyendo la caché de validación", exc_info=True)
                payload = None

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
        ttl = _ttl_validacion(payload, request.app.state.auth_cache_ttl)
        # `desconocido` (la base de auth-service no respondió) tampoco se cachea: un
        # fallo transitorio no debe bloquear a la entidad durante todo el TTL.
        if (
            redis is not None
            and payload.get("valid", False)
            and ttl > 0
            and payload.get("tenant_estado") != "desconocido"
        ):
            try:
                await redis.set(
                    cache_key,
                    _sellar_entrada_cache(payload, cache_key, mac_secret),
                    ex=ttl,
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


def _mac_cache(cache_key: str, cuerpo: str, secreto: str) -> str:
    """MAC (HMAC-SHA256) de la entrada, ligado a SU clave.

    Incluir `cache_key` impide mover una entrada válida de un token a otro. El
    secreto es EXCLUSIVO del gateway (`AUTH_CACHE_MAC_SECRET`): quien solo tenga
    la contraseña de Redis no puede forjar una identidad cacheada.
    """
    return hmac.new(
        secreto.encode(), f"{cache_key}\x1e{cuerpo}".encode(), hashlib.sha256
    ).hexdigest()


def _sellar_entrada_cache(payload: dict, cache_key: str, secreto: str) -> str:
    cuerpo = json.dumps(payload)
    return f"{_mac_cache(cache_key, cuerpo, secreto)}.{cuerpo}"


def _leer_entrada_cache(crudo: str | bytes, cache_key: str, secreto: str) -> dict | None:
    """Devuelve el payload si el MAC verifica y es un SÍ; si no, `None` (se
    consulta a auth-service). Una entrada sin MAC, con MAC ajeno o con
    `valid != True` se descarta."""
    texto = crudo.decode() if isinstance(crudo, bytes) else crudo
    mac, sep, cuerpo = texto.partition(".")
    if not sep or not hmac.compare_digest(mac, _mac_cache(cache_key, cuerpo, secreto)):
        return None
    payload = json.loads(cuerpo)
    if not isinstance(payload, dict) or payload.get("valid") is not True:
        return None
    return payload


def _ttl_validacion(payload: dict, ttl_maximo: int) -> int:
    """TTL acotado por el `exp` del token: nunca sobrevive a su caducidad.

    Sin `expires_in` utilizable devuelve 0 = NO cachear (antes usaba el máximo,
    y como `/validate` nunca lo emitía, el tope por `exp` era inerte).
    """
    restante = payload.get("expires_in")
    if isinstance(restante, int) and not isinstance(restante, bool) and restante > 0:
        return max(1, min(ttl_maximo, restante))
    return 0


#: Veredicto por estado de la entidad: (HTTP, error, detalle). Ausente o desconocido
#: es fail-closed: un auth-service que no emite el campo no abre la puerta.
_VETO_ENTIDAD: dict[str, tuple[int, str, str]] = {
    "inactiva": (403, "tenant_inactivo", "Esta entidad no está activa en la plataforma."),
    "no_provisionada": (
        503,
        "tenant_no_provisionado",
        "Esta entidad aún no está disponible.",
    ),
}
_VETO_DESCONOCIDO = (
    503,
    "tenant_estado_no_disponible",
    "No se pudo comprobar el estado de la entidad.",
)


def _veto_por_estado_de_entidad(payload: dict) -> JSONResponse | None:
    """`None` si se sigue; si no, la respuesta que corta la petición.

    Sin `tenant_slug` (superadmin sin entidad) se sigue: los servicios exigen la
    cabecera y el slug vacío no resuelve ningún schema.
    """
    if not payload.get("tenant_slug"):
        return None
    estado = payload.get("tenant_estado")
    if estado == "operativa":
        return None
    codigo, error, detalle = _VETO_ENTIDAD.get(str(estado), _VETO_DESCONOCIDO)
    return JSONResponse(
        status_code=codigo, content={"error": error, "detail": detalle, "status": codigo}
    )


async def _continuar_con_identidad(request: Request, call_next, payload: dict) -> Response:
    """Inyecta la identidad validada y sigue la cadena.

    Extraído para que el camino con caché y el camino sin ella construyan las
    cabeceras en UN solo sitio: dos copias divergen, y la que se olvide de añadir
    una cabecera nueva fallará solo cuando la caché esté caliente — es decir, en
    producción y no en las pruebas.
    """
    # B9-1 (ADR-035): una entidad no operativa no se atiende. Va AL PRINCIPIO para que
    # el camino con caché y el camino sin ella pasen por el mismo veredicto. La
    # suspensión surte efecto como mucho tras `auth_cache_ttl` (15 s por defecto).
    veto = _veto_por_estado_de_entidad(payload)
    if veto is not None:
        return veto
    roles = payload.get("roles", [])
    request.state.auth_headers = {
        "X-User-Id": payload.get("user_id", ""),
        "X-Username": payload.get("username", ""),
        "X-Tenant-Slug": payload.get("tenant_slug") or "",
        "X-User-Roles": ",".join(roles),
        "X-Canal": derive_canal(
            request.url.path,
            request.headers.get(identidad.CABECERA_ORIGEN),
            tenant=payload.get("tenant_slug") or "",
            authorization=request.headers.get("authorization", ""),
        ),
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
    # Y se FIRMAN.
    #
    # Sin firma, estas cabeceras son una afirmación, no una identidad: el gateway
    # no las reenvía si vienen del cliente, así que desde fuera no se pueden
    # forjar, pero dentro de la red cualquiera que alcance el puerto de un
    # servicio podía hablarle diciendo ser quien quisiera y de la entidad que
    # quisiera. Es el mismo cierre que el sobre de eventos, en la otra dirección.
    request.state.auth_headers = identidad.firmar(request.state.auth_headers)
    # La IP real, además, con el secreto de ORIGEN: la verifica el mcp-server, que
    # no posee el secreto maestro de identidad (AREA-11-08).
    request.state.auth_headers = identidad.firmar_ip_real(request.state.auth_headers)

    return await call_next(request)
