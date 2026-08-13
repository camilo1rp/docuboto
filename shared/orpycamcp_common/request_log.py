# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Registro estructurado por petición en los servicios de aplicación.

Complemento necesario del `X-Request-Id` que el gateway ahora genera **antes**
del proxy (`api-gateway/app/middleware/request_context.py`). Que el
identificador llegue no sirve de nada si ningún servicio lo escribe: sería el
mismo defecto de partida —una tubería puesta por la que no pasa nada— movido un
salto más allá.

Cada servicio emite una línea JSON por petición con el identificador, el tenant,
el usuario, la ruta, el estado y la duración. Con eso, una incidencia se sigue
de un extremo a otro filtrando por un solo valor en los once servicios.

El identificador queda además en un ``ContextVar``, para que cualquier registro
que un servicio emita durante la petición pueda etiquetarse con él
(``request_id_actual()``) sin pasarlo por la firma de veinte funciones.

**Qué NO se registra**: la cadena de consulta ni el cuerpo. Llevan números de
radicado, asuntos y términos de búsqueda; los registros de un servicio no son
sitio para el contenido documental, que además está clasificado. El asiento con
valor probatorio es ``audit_log``, no esto.
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: ContextVar[str] = ContextVar("orpycamcp_request_id", default="")


def request_id_actual() -> str:
    """Identificador de la petición en curso, o cadena vacía fuera de una."""
    return _request_id.get()


class RequestLogMiddleware:
    """Una línea JSON por petición, con el hilo de traza del gateway."""

    def __init__(self, app: ASGIApp, *, servicio: str) -> None:
        self.app = app
        self.servicio = servicio
        self.logger = logging.getLogger(f"{servicio}.access")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cabeceras = {k.lower(): v for k, v in scope["headers"]}
        request_id = cabeceras.get(b"x-request-id", b"").decode("latin-1", "replace")
        tenant = cabeceras.get(b"x-tenant-slug", b"").decode("latin-1", "replace")
        usuario = cabeceras.get(b"x-user-id", b"").decode("latin-1", "replace")

        token = _request_id.set(request_id)
        inicio = time.perf_counter()
        estado: dict[str, Any] = {"status": 0}

        async def send_observado(message: Message) -> None:
            if message["type"] == "http.response.start":
                estado["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_observado)
        except Exception:
            estado["status"] = 500
            raise
        finally:
            # En `finally` a propósito: una excepción que sube es justo la
            # petición que hay que poder encontrar después.
            self.logger.info(
                json.dumps(
                    {
                        "servicio": self.servicio,
                        "request_id": request_id or None,
                        "method": scope.get("method", ""),
                        "path": scope.get("path", ""),
                        "status": estado["status"],
                        "duracion_ms": round((time.perf_counter() - inicio) * 1000, 1),
                        "tenant": tenant or None,
                        "usuario": usuario or None,
                    },
                    ensure_ascii=False,
                )
            )
            _request_id.reset(token)
