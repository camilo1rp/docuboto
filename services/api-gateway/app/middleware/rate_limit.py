# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import re
from datetime import UTC, datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.client_ip import resolve_client_ip

GLOBAL_RATE_KEY = "ratelimit:global"

# Puertas ciudadanas de radicación: crean radicados SIN autenticar y consumen
# números que no se reutilizan. Llevan cubo propio, mucho más estrecho que el
# general, y por eso se identifican por RUTA y no por permiso — no hay usuario a
# quien atribuirlas.
#
# La lista se escribe por ADMISIÓN y cubre las DOS puertas, no solo la histórica:
# `pqrs` es la ruta legada y `ventanilla` la vigente (la que además acepta
# anexos, o sea la cara). Cuando la ventanilla sustituyó a PQRS, el cubo estrecho
# se quedó vigilando la puerta que ya casi nadie usa: el tope caía sobre el
# endpoint barato mientras el caro heredaba el general de 100/min. Toda puerta
# anónima que consuma numeración oficial entra aquí el día que nace.
_RADICACION_ANONIMA_RE = re.compile(r"^/api/v1/public/[^/]+/(pqrs|ventanilla)/?$")

# El chequeo de salud es el comando del healthcheck del contenedor y del balanceador.
# Queda fuera del cubo GLOBAL —no del suyo por IP— para que la saturación del
# global no vuelva «no apto» a un gateway que está perfectamente sano.
_SALUD_RE = re.compile(r"^/health(/ready)?$")


def _current_minute_window() -> str:
    now = datetime.now(tz=UTC)
    return f"{now.year}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}"


def _client_ip(request: Request) -> str:
    """IP del cliente por saltos de CONFIANZA (ver `app/core/client_ip.py`).

    La versión anterior tomaba el primer valor de `X-Forwarded-For` sin más, así
    que el limitador se evadía rotando la cabecera: cada petición parecía venir de
    una IP distinta y solo quedaba en pie el tope global — que el mismo atacante
    podía agotar para tirar el servicio para todos.
    """
    return resolve_client_ip(request, getattr(request.app.state, "trusted_proxies", ""))


def _demasiadas_peticiones() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": "Demasiadas peticiones; espere un momento antes de reintentar",
            "status": 429,
        },
        headers={"Retry-After": "60"},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        redis = getattr(request.app.state, "redis", None)

        if redis is None:
            # Fail open — no Redis, skip rate limiting
            return await call_next(request)

        minute = _current_minute_window()
        ip = _client_ip(request)
        ip_key = f"ratelimit:{ip}:{minute}"
        global_key = f"{GLOBAL_RATE_KEY}:{minute}"

        per_ip_limit: int = request.app.state.rate_limit_per_ip
        global_limit: int = request.app.state.rate_limit_global

        # Cubo dedicado y estrecho para las altas anónimas de radicación (PQRS y
        # ventanilla). Va en la MISMA clave por-IP (no en una aparte) para que el
        # tope estricto sustituya al general en esas rutas en vez de sumarse a él.
        es_radicacion_anonima = request.method == "POST" and bool(
            _RADICACION_ANONIMA_RE.match(request.url.path)
        )
        if es_radicacion_anonima:
            per_ip_limit = request.app.state.rate_limit_pqrs_per_ip
            ip_key = f"ratelimit:pqrs:{ip}:{minute}"

        try:
            # El cubo por IP se resuelve PRIMERO y, si rechaza, se sale sin tocar
            # el global. Antes ambos INCR iban en el mismo pipeline y la decisión
            # venía después: cada petición que el propio limitador rechazaba
            # seguía sumando al contador compartido, de modo que un anónimo desde
            # UNA sola IP empujaba el global hasta el tope y dejaba en 429 a todos
            # los usuarios de todas las entidades. Un limitador no puede gastar en
            # el cubo de terceros el tráfico que ya declaró abusivo.
            pipe = redis.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 60)
            ip_count: int = (await pipe.execute())[0]

            if ip_count > per_ip_limit:
                return _demasiadas_peticiones()

            # El chequeo de salud sí cuenta en su cubo por IP (arriba), pero no
            # entra en el global: si lo hiciera, un gateway sano se declararía
            # enfermo justo cuando más importa distinguir una cosa de la otra.
            if _SALUD_RE.match(request.url.path):
                return await call_next(request)

            pipe = redis.pipeline()
            pipe.incr(global_key)
            pipe.expire(global_key, 60)
            global_count: int = (await pipe.execute())[0]

            if global_count > global_limit:
                return _demasiadas_peticiones()
        except Exception:
            # Fail open — Redis error, skip rate limiting
            pass

        return await call_next(request)
