# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Identificador de petición y registro estructurado del gateway.

**El defecto que corrige.** El gateway ya generaba un `X-Request-Id`… en la
*respuesta*:

    @app.middleware("http")
    async def add_gateway_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Request-Id"] = str(uuid.uuid4())   # ← aquí

Para entonces la petición ya se había reenviado, los servicios habían escrito
sus registros y sus asientos de auditoría, y el identificador no había llegado a
ninguno. `x-request-id` figura en `FORWARDED_REQUEST_HEADERS` desde siempre, lo
cual hacía la carencia todavía más invisible: la tubería estaba puesta y no
pasaba nada por ella. El resultado es que un incidente en producción no se puede
seguir de un extremo a otro — hay once servicios y ningún hilo que los cosa.

**La corrección.** El identificador se resuelve *antes* que nada, se escribe en
las cabeceras del propio `scope` (así el proxy lo reenvía por la lista blanca
que ya existía) y se devuelve **el mismo** al cliente.

**Sobre aceptar el que envía el cliente.** Se acepta, porque el frontend hace SSR
y su petición al gateway es la continuación de la del navegador: sin eso, la
traza se parte justo en el salto que más cuesta reconstruir. No es una
credencial y no decide nada — solo etiqueta registros —, pero igual se sanea
(alfanuméricos, guiones y puntos, 64 caracteres) para que nadie meta saltos de
línea en los registros ni un valor gigante que se copie once veces.

Este middleware es ASGI puro a propósito, no `BaseHTTPMiddleware`: envuelve las
descargas en flujo (anexos, ZIP de expediente, AIP) sin materializarlas, que es
la propiedad que el bloque 4 costó conseguir.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("gateway.access")

GATEWAY_VERSION = "1.0.0"

_ID_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def sanear_request_id(crudo: str | None) -> str | None:
    """Devuelve el identificador si es aceptable, o `None` para generar uno."""
    if not crudo:
        return None
    crudo = crudo.strip()
    return crudo if _ID_VALIDO.match(crudo) else None


class RequestContextMiddleware:
    """Asigna `X-Request-Id` antes del proxy y registra cada petición."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        entrante = None
        for clave, valor in scope["headers"]:
            if clave.lower() == b"x-request-id":
                entrante = valor.decode("latin-1", "replace")
                break
        request_id = sanear_request_id(entrante) or str(uuid.uuid4())

        # Se reescribe la cabecera en el scope —no se añade— para que un cliente
        # no pueda colar dos: el upstream leería la primera y los registros de
        # aquí la segunda, y la traza señalaría a peticiones distintas.
        cabeceras = [(k, v) for k, v in scope["headers"] if k.lower() != b"x-request-id"]
        cabeceras.append((b"x-request-id", request_id.encode("latin-1")))
        scope["headers"] = cabeceras

        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        inicio = time.perf_counter()
        estado: dict[str, Any] = {"status": 0}

        async def send_con_cabeceras(message: Message) -> None:
            if message["type"] == "http.response.start":
                estado["status"] = message["status"]
                cabeceras_respuesta = MutableHeaders(scope=message)
                cabeceras_respuesta["x-request-id"] = request_id
                cabeceras_respuesta["x-gateway-version"] = GATEWAY_VERSION
            await send(message)

        try:
            await self.app(scope, receive, send_con_cabeceras)
        except Exception:
            estado["status"] = 500
            self._registrar(scope, request_id, estado["status"], inicio)
            raise
        else:
            self._registrar(scope, request_id, estado["status"], inicio)

    def _registrar(self, scope: Scope, request_id: str, status: int, inicio: float) -> None:
        """Una línea JSON por petición, con quién y sobre qué entidad.

        El tenant y el usuario salen de `state["auth_headers"]`, que el
        middleware de JWT rellena con la identidad **ya validada**. Si la
        petición no llegó a autenticarse (401, ruta pública) sencillamente no
        están, y eso también es información.

        No se registra la cadena de consulta: lleva filtros con números de
        radicado y términos de búsqueda, y los registros del gateway no son un
        sitio donde deba quedar el contenido de las consultas de nadie.
        """
        auth = (scope.get("state") or {}).get("auth_headers") or {}
        registro = {
            "request_id": request_id,
            "method": scope.get("method", ""),
            "path": scope.get("path", ""),
            "status": status,
            "duracion_ms": round((time.perf_counter() - inicio) * 1000, 1),
            "tenant": auth.get("X-Tenant-Slug") or None,
            "usuario": auth.get("X-User-Id") or None,
        }
        logger.info(json.dumps(registro, ensure_ascii=False))
