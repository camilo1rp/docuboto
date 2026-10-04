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


_idempotency_key: ContextVar[str] = ContextVar("orpycamcp_idempotency_key", default="")


_canal: ContextVar[str] = ContextVar("orpycamcp_canal", default="")

#: Canales que un servicio acepta de la cabecera `X-Canal` (los de persona; 'system'
#: es el de las llamadas internas y se decide en cada llamada, no se hereda).
_CANALES_HEREDABLES = frozenset({"api", "ui", "mcp"})


def canal_actual() -> str:
    """Canal de origen de la petición en curso (`X-Canal` del gateway), o vacío.

    El gateway lo deriva del lado del servidor (D-03) y va dentro de la identidad
    firmada. Lo lee `audit.append` cuando el llamante declara
    `canal=CANAL_PETICION`: así el acto que el asistente confirma en nombre de una
    persona se asienta 'mcp' y no 'api' (AREA-11-04). Los procesos de fondo lo
    dejan en 'system' con :func:`canal_de_fondo` (AREA-03-07).
    """
    return _canal.get()


def canal_de_fondo() -> None:
    """Marca la tarea asyncio en curso como proceso de FONDO: su canal es 'system'.

    Se llama al principio del cuerpo de cada bucle periódico y de cada consumidor de
    eventos. Una tarea tiene su PROPIA copia del contexto, así que fijar el canal aquí
    no se filtra a las peticiones ni a otras tareas. Sin esto, un acto de un job que
    asienta con ``audit.CANAL_PETICION`` (una función que sirve tanto a una petición
    como al job) heredaba el vacío y se asentaba 'api': la traza mentía sobre el
    origen (AREA-03-07). No hay que llamarlo en una tarea lanzada DESDE una petición
    (p. ej. un lote en segundo plano): esa sí hereda el canal de la persona.
    """
    _canal.set("system")


def request_id_actual() -> str:
    """Identificador de la petición en curso, o cadena vacía fuera de una."""
    return _request_id.get()


def idempotency_key_actual() -> str:
    """`Idempotency-Key` de la petición en curso, o cadena vacía.

    La emite el mcp-server (el `jti` de la propuesta firmada) y sirve para
    correlacionar el asiento de auditoría con la propuesta que lo originó
    (AREA-11-04); ver `audit.append`. Recortada a 128 caracteres.
    """
    return _idempotency_key.get()


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

        idem = cabeceras.get(b"idempotency-key", b"").decode("latin-1", "replace")[:128]
        canal = cabeceras.get(b"x-canal", b"").decode("latin-1", "replace")
        token_canal = _canal.set(canal if canal in _CANALES_HEREDABLES else "")
        token = _request_id.set(request_id)
        token_idem = _idempotency_key.set(idem)
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
            _canal.reset(token_canal)
            _idempotency_key.reset(token_idem)
