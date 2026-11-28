# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que hacía el api-gateway en cada petición, ahora en el proceso (ADR-047).

El límite de tasa por IP vive en `edge` (nginx, `limit_req`). Aquí quedan tres
cosas:

* **Cuota por persona** (`UserRateLimitMiddleware`, AREA-02-01): nginx no sabe
  quién es nadie, y el cubo por IP junta a toda una oficina detrás de un NAT. Un
  solo proceso: la ventana vive en memoria, sin almacén externo.

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

import json
import os
import re
import time
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


def rate_limit_per_user() -> int:
    """Peticiones por persona y minuto (`RATE_LIMIT_PER_USER`, 120 por defecto)."""
    try:
        return max(1, int(os.environ.get("RATE_LIMIT_PER_USER", "120")))
    except ValueError:
        return 120


_DEMASIADAS = json.dumps(
    {
        "error": "rate_limit_exceeded",
        "detail": "Demasiadas peticiones; espere un momento antes de reintentar",
        "status": 429,
    }
).encode()


class UserRateLimitMiddleware:
    """Cubo por persona y minuto, con la identidad que ya validó `UsuarioActualMiddleware`.

    Va DENTRO de él (se registra antes): sin persona —ruta pública, token que no
    pasó— no cuenta aquí, porque juntar a los anónimos en una clave vacía es el
    defecto que este eje cerraba; ese tráfico lo cubre el cubo por IP de `edge`.
    La clave lleva el tenant: el id de usuario es único dentro de su entidad.
    """

    def __init__(self, app: ASGIApp, *, limit: int | None = None) -> None:
        self.app = app
        self._limit = limit
        self._ventana = 0
        self._cuentas: dict[tuple[str, str], int] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        usuario = (scope.get("state") or {}).get("usuario")
        if usuario is None or getattr(usuario, "id", None) is None:
            await self.app(scope, receive, send)
            return
        minuto = int(time.time() // 60)
        if minuto != self._ventana:
            self._ventana = minuto
            self._cuentas.clear()
        clave = (str(getattr(usuario, "tenant_slug", "")), str(usuario.id))
        cuenta = self._cuentas.get(clave, 0) + 1
        self._cuentas[clave] = cuenta
        if cuenta > (self._limit or rate_limit_per_user()):
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(_DEMASIADAS)).encode()),
                        (b"retry-after", b"60"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": _DEMASIADAS})
            return
        await self.app(scope, receive, send)
