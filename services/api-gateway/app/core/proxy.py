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

# Whitelist of client request headers that are forwarded to upstream services.
# Headers NOT listed here are silently dropped at this trust boundary.
#
# Security note (D-03): "x-canal" is intentionally excluded so that clients
# cannot forge the audit canal.  The gateway injects X-Canal server-side via
# JWTValidationMiddleware.derive_canal() and adds it through extra_headers.
FORWARDED_REQUEST_HEADERS = {
    "authorization",
    "content-type",
    "user-agent",
    "x-forwarded-for",
    "x-request-id",
    "accept",
    "accept-language",
    "accept-encoding",
    # E20 F5: firma HMAC del webhook ENTRANTE del operador postal
    # (`POST /api/v1/public/postal/callback/{slug}`). Es una credencial de
    # AUTENTICACIÓN del operador (no del usuario) que document-service verifica
    # sobre el cuerpo crudo; el gateway debe reenviarla intacta o todo callback
    # legítimo fallaría 401 (la puerta HMAC quedaría inalcanzable e2e).
    "x-orpycamcp-signature",
}

# Whitelist of upstream response headers forwarded back to the client.
# Headers NOT listed here are silently dropped — same trust boundary as the
# request side, and with the same failure mode: what is missing is invisible.
#
# `x-total-count` y `x-truncated` NO son cabeceras opcionales de conveniencia,
# son parte de contratos que el sistema documenta y que dependen de ellas:
#
#   - `X-Total-Count` es la convención de paginación declarada en
#     `docs/es/api.md` ("respuesta incluye `X-Total-Count`"). La emiten 31
#     routers y la consume el frontend en ~115 puntos. Sin reenviarla, el
#     total colapsa al tamaño de la página: TODA pantalla paginada cree que
#     hay una sola página y las siguientes quedan inalcanzables.
#   - `X-Truncated` es la garantía de honestidad de las tres DLQ (ADR-021):
#     el backend avisa con ella de que hay eventos pendientes más antiguos que
#     NO caben en la respuesta. Sin reenviarla, el banner que lo advierte es
#     código muerto y la pantalla afirma en silencio que se ve todo.
#
# Es el gemelo, del lado de respuesta, de la lección de FORWARDED_REQUEST_HEADERS
# (E20: una cabecera propia que cruza el gateway necesita estar en la lista Y
# tener test de contrato). El test vive en `tests/test_gateway.py`.
FORWARDED_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "cache-control",
    "etag",
    "last-modified",
    "location",
    "x-total-count",
    "x-truncated",
}


def _build_upstream_headers(request: Request, extra: dict[str, str]) -> dict[str, str]:
    headers: dict[str, str] = {}

    for key, value in request.headers.items():
        if key.lower() in FORWARDED_REQUEST_HEADERS:
            headers[key] = value

    client_ip = request.headers.get("x-forwarded-for")
    if not client_ip and request.client:
        client_ip = request.client.host
    if client_ip:
        headers["x-forwarded-for"] = client_ip

    headers.update(extra)
    return headers


async def proxy_request(
    http_client: httpx.AsyncClient,
    upstream_url: str,
    request: Request,
    extra_headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Response:
    headers = _build_upstream_headers(request, extra_headers or {})

    body = await request.body()

    # `timeout` override se pasa por-petición (p. ej. el asistente E18, cuyo loop
    # LLM excede el timeout global del cliente). Sin override se usa el del cliente.
    request_kwargs: dict[str, object] = {
        "method": request.method,
        "url": upstream_url,
        "headers": headers,
        "content": body,
        "params": dict(request.query_params),
    }
    if timeout is not None:
        request_kwargs["timeout"] = timeout

    upstream_response = await http_client.request(**request_kwargs)

    response_headers: dict[str, str] = {}
    for key, value in upstream_response.headers.items():
        if key.lower() in FORWARDED_RESPONSE_HEADERS:
            response_headers[key] = value

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
