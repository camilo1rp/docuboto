# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from collections.abc import AsyncIterator

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

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
    # AREA-11-04: el `jti` de la propuesta que el mcp-server emite como
    # `Idempotency-Key`. Los servicios lo copian a `payload.correlacion` solo en los
    # asientos de canal 'mcp' (que es el gateway quien decide, no esta cabecera), así
    # que reenviarla no concede nada: sin ella la correlación moría en el gateway.
    "idempotency-key",
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
# `x-total-count` y `x-faltantes` NO son cabeceras opcionales de conveniencia,
# son parte de contratos que el sistema documenta y que dependen de ellas:
#
#   - `X-Total-Count` es la convención de paginación declarada en
#     `docs/es/api.md` ("respuesta incluye `X-Total-Count`"). La emiten 31
#     routers y la consume el frontend en ~115 puntos. Sin reenviarla, el
#     total colapsa al tamaño de la página: TODA pantalla paginada cree que
#     hay una sola página y las siguientes quedan inalcanzables.
#   - `X-Faltantes` es la garantía equivalente de la previsualización de un
#     documento de salida (E23, RF-PRO-01): dice qué campos del vocabulario
#     quedaron SIN valor. El cuerpo de esa respuesta es el PDF, así que no hay
#     otro sitio donde ponerla. Sin reenviarla, la pantalla enseña un documento
#     con huecos y nadie sabe que son huecos — el oficio saldría con un espacio
#     en blanco donde debía ir un dato, y el fallo llegaría al ciudadano.
#   - `X-Sellado-Diferido-Count` dice cuántos índices electrónicos acabó
#     sellando el job de reconciliación en vez del acto del cierre. La cola de
#     pendientes solo dice qué falta AHORA, y el job borra su propio rastro al
#     reparar: sin este número, un fallo permanente del sellado síncrono se ve
#     igual que un sistema que nunca falla — que es exactamente lo que pasó
#     durante dos días (ver `IndexRepository.count_sellado_diferido`).
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
    "x-faltantes",
    "x-sellado-diferido-count",
    # La respuesta se reenvía **cruda** (`aiter_raw`), sin descomprimir: es lo que
    # permite no materializarla en memoria. Por eso `content-encoding` tiene que
    # viajar — sin ella el cliente recibiría bytes gzip anunciados como texto plano
    # y no podría leerlos. Antes se descomprimía con `.content` y se reenviaba el
    # `content-length` del cuerpo COMPRIMIDO: los dos valores no casaban.
    "content-encoding",
    # Descargas de anexos, export ZIP, AIP y actas: sin esta cabecera el navegador
    # inventa el nombre del fichero a partir de la URL.
    "content-disposition",
    "accept-ranges",
    "content-range",
    # Contadores del paquete interoperable (E11 INT-01, `document-service`
    # `routers/export.py`). El ZIP viaja como cuerpo, así que lo único que dice
    # CUÁNTO se exportó —y cuántos radicados quedaron fuera por no-read-up— son
    # estas cabeceras. Sin ellas la pantalla de interoperabilidad pintaba
    # «— radicado(s) exportado(s)» sobre un paquete de seis, y el conteo de
    # excluidos (Ley 1712/2014 arts. 18-19) desaparecía sin dejar rastro: quien
    # exporta no llegaba a enterarse de que su clearance recortó el paquete.
    "x-export-count",
    "x-export-excluded",
    # El UUID identifica el paquete en el asiento de auditoría de la
    # exportación: es lo que permite casar el fichero descargado con el acto.
    "x-export-uuid",
    # `nosniff` de la descarga CMIS (`document-service/app/routers/cmis.py`).
    # Ahí el tipo se sirve desde una lista de ADMISIÓN y lo que no está en ella
    # sale como `application/octet-stream` + `attachment`. Si el gateway se
    # comiera esta cabecera, el navegador volvería a adivinar el tipo por el
    # contenido y un fichero subido por la ventanilla podría ejecutarse como
    # HTML en el origen del sistema — justo lo que la lista vino a impedir.
    # Nadie más la pone: ni el gateway ni nginx.
    "x-content-type-options",
    # Alias deprecados por nombre de dependencia (B5, ADR-032): el servicio
    # responde `Deprecation: true` + `Link: <...>; rel="deprecation"` cuando el
    # cliente usó `to_dept`/`dept` en vez del código. Si el gateway se las come,
    # el cliente nunca se entera de que debe migrar y el alias no se puede retirar
    # con datos. (RFC 9745 / RFC 8288.)
    "deprecation",
    "sunset",
    "link",
}


class BodyTooLargeError(Exception):
    """El cuerpo de la petición superó `max_request_body_bytes`."""


async def _limited_stream(request: Request, max_bytes: int) -> AsyncIterator[bytes]:
    """Reemite el cuerpo por trozos, abortando si supera *max_bytes*.

    El chequeo por `Content-Length` (en `proxy_request`) rechaza antes de leer
    nada, pero no basta: una petición `Transfer-Encoding: chunked` no lo trae, y
    el que quiere agotar la memoria del proceso es justamente quien lo omite.
    """
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise BodyTooLargeError(str(total))
        yield chunk


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
    max_body_bytes: int | None = None,
) -> Response:
    """Reenvía la petición al upstream **por trozos**, en ambos sentidos.

    Antes se hacía `await request.body()` y `upstream_response.content`: el cuerpo
    de subida y el de bajada se materializaban **enteros en memoria** del gateway,
    y storage hacía una segunda copia del de subida. Con `memory: 1G` por réplica y
    sin ningún tope de tamaño, un PDF grande —o varios a la vez— no daba un error:
    mataba el contenedor. Ahora ambos sentidos van en flujo y el tope
    (`max_body_bytes`) es lo que acota el gasto.
    """
    headers = _build_upstream_headers(request, extra_headers or {})

    if max_body_bytes is not None:
        # Rechazo barato ANTES de leer un solo byte, cuando el cliente declara el
        # tamaño. El chequeo real durante el flujo está en `_limited_stream`.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_body_bytes:
            return _too_large(int(declared), max_body_bytes)

    # `content` acepta un iterador asíncrono: httpx lo consume a medida que escribe
    # en el socket, sin acumularlo. GET/DELETE sin cuerpo pasan igual — el stream
    # simplemente no produce trozos.
    content: AsyncIterator[bytes] = (
        _limited_stream(request, max_body_bytes)
        if max_body_bytes is not None
        else request.stream()
    )

    # `timeout` override se pasa por-petición (p. ej. el asistente E18, cuyo loop
    # LLM excede el timeout global del cliente). Sin override se usa el del cliente:
    # USE_CLIENT_DEFAULT es el centinela de httpx para "no lo toques", y NO es
    # intercambiable con None — None significa "sin límite de tiempo", que aquí
    # dejaría cada petición al upstream colgada indefinidamente.
    # La cadena de consulta se reenvía como LISTA DE PARES y no como `dict()`.
    # Es un multidict: `dict()` se queda con el último valor de cada clave, así
    # que `?status=distributed&status=in_progress` salía de aquí convertido en
    # `{'status': 'in_progress'}` —comprobado contra Starlette—.
    #
    # El modo de fallo es el peor que hay: 200, sin error y con menos filas.
    # Quien pidiera dos estados recibiría uno y creería que ese es el total. Y
    # dejaba INALCANZABLE desde fuera cualquier parámetro repetible que un
    # servicio declare, por bien que lo declare: el frontend y el mcp-server
    # hablan siempre por aquí.
    #
    # No lo cazaba ninguna suite porque los tests de cada servicio golpean su
    # propia app, donde el multidict llega intacto: el defecto vivía en el
    # SALTO, que no es de nadie.
    #
    # El tipo se anota porque `list` es invariante: `list[tuple[str, str]]` no
    # satisface la firma de httpx, que admite valores de varios tipos.
    consulta: list[tuple[str, str | int | float | bool | None]] = list(
        request.query_params.multi_items()
    )

    upstream_request = http_client.build_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=content,
        params=consulta,
        timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
    )
    try:
        upstream_response = await http_client.send(upstream_request, stream=True)
    except BodyTooLargeError as exc:
        return _too_large(int(str(exc)), max_body_bytes or 0)

    response_headers: dict[str, str] = {}
    for key, value in upstream_response.headers.items():
        if key.lower() in FORWARDED_RESPONSE_HEADERS:
            response_headers[key] = value

    # `aiter_raw`, no `aiter_bytes`: reenvía los bytes tal cual llegan (sin
    # descomprimir), que es lo coherente con reenviar `content-encoding` y
    # `content-length` del upstream. `BackgroundTask` cierra la respuesta del
    # upstream cuando el cliente termina de leer — sin eso la conexión del pool
    # se quedaría retenida, y con ella el pool entero al cabo de un rato.
    return StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream_response.aclose),
    )


def _too_large(recibido: int, maximo: int) -> Response:
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=413,
        content={
            "error": "request_entity_too_large",
            "detail": (
                f"El cuerpo de la petición ({recibido} bytes) supera el máximo "
                f"admitido ({maximo} bytes)."
            ),
            "status": 413,
        },
    )
