# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
Contexto de la petición entrante que las llamadas salientes necesitan.

Middleware ASGI puro (no `BaseHTTPMiddleware`) para que el `ContextVar` llegue
intacto al manejador, incluido el transporte MCP streamable montado en `/mcp`.
"""

from __future__ import annotations

from orpycamcp_common import identidad
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services import origen


class ContextoEntranteMiddleware:
    """Recoge la IP del cliente real, firmada por el gateway, para reenviarla."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cabeceras = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        token = origen.fijar_ip_cliente(identidad.ip_real_firmada(cabeceras))
        try:
            await self.app(scope, receive, send)
        finally:
            origen.soltar_ip_cliente(token)
