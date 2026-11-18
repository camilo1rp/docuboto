# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que hacía el api-gateway en cada respuesta, ahora en el proceso (ADR-047).

El límite de tasa vive en `edge` (nginx, `limit_req`). Aquí quedan dos cosas:

* **`X-Request-Id`** antes que nada: se acepta el del cliente si es sano (el SSR
  continúa la petición del navegador y la traza no debe partirse en ese salto)
  y si no se genera uno. Se reescribe en el `scope` —no se añade—, así un
  cliente no cuela dos, y se devuelve el mismo.
* **Cabeceras de endurecimiento** de una API JSON: `nosniff`, sin marcos y sin
  `Referer`. Se fijan solo si el router no puso la suya (la descarga CMIS
  declara su propio `nosniff`). HSTS lo pone `edge`, que termina el TLS.

ASGI puro y no `BaseHTTPMiddleware`: envuelve las descargas en flujo (anexos,
ZIP de expediente, AIP) sin materializarlas.
"""

from __future__ import annotations

import re
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_ID_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

RESPONSE_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def sanitize_request_id(raw: str | None) -> str | None:
    """El identificador si es aceptable, o `None` para generar uno."""
    if not raw:
        return None
    raw = raw.strip()
    return raw if _ID_VALIDO.match(raw) else None


class SecurityHeadersMiddleware:
    """Asigna `X-Request-Id` y endurece la respuesta."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        entrante = None
        for clave, valor in scope.get("headers", []):
            if clave.lower() == b"x-request-id":
                entrante = valor.decode("latin-1", "replace")
                break
        request_id = sanitize_request_id(entrante) or str(uuid.uuid4())
        cabeceras = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"x-request-id"]
        cabeceras.append((b"x-request-id", request_id.encode("latin-1")))
        scope["headers"] = cabeceras

        async def send_endurecido(message: Message) -> None:
            if message["type"] == "http.response.start":
                respuesta = MutableHeaders(scope=message)
                respuesta["x-request-id"] = request_id
                for nombre, valor in RESPONSE_HEADERS.items():
                    if nombre not in respuesta:
                        respuesta[nombre] = valor
            await send(message)

        await self.app(scope, receive, send_endurecido)
