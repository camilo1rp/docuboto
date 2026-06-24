# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from datetime import UTC, datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

GLOBAL_RATE_KEY = "ratelimit:global"


def _current_minute_window() -> str:
    now = datetime.now(tz=UTC)
    return f"{now.year}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}"


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


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

        try:
            pipe = redis.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 60)
            pipe.incr(global_key)
            pipe.expire(global_key, 60)
            results = await pipe.execute()

            ip_count: int = results[0]
            global_count: int = results[2]

            if ip_count > per_ip_limit or global_count > global_limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "detail": "Too many requests, please slow down",
                        "status": 429,
                    },
                    headers={"Retry-After": "60"},
                )
        except Exception:
            # Fail open — Redis error, skip rate limiting
            pass

        return await call_next(request)
