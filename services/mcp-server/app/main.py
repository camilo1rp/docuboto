# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator

import httpx
from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from orpycamcp_common import request_log
from orpycamcp_common import secrets as common_secrets

from app import mcp_app
from app.core.config import settings
from app.core.contexto import ContextoEntranteMiddleware
from app.routers.health import router as health_router
from app.routers.mcp import router as mcp_router

logging.basicConfig(level=settings.log_level.upper())

# Transporte HTTP streamable del protocolo MCP (clientes remotos). Stateless +
# JSON response: cada POST es autocontenido, lo que permite propagar el contexto
# de sesión (cabeceras → ContextVar) de forma fiable dentro de la misma petición.
_session_manager = StreamableHTTPSessionManager(
    app=mcp_app.server, json_response=True, stateless=True
)


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode()
    return None


async def _handle_mcp(scope, receive, send) -> None:
    """Endpoint MCP streamable: inyecta el contexto de sesión desde las cabeceras.

    Ya NO lee `X-User-Permissions` ni `X-User-Roles`. La primera no la produce
    nadie —el gateway la descarta por forjable— así que llegaba siempre vacía y
    este transporte ofrecía únicamente las tools sin permiso. La segunda sí
    llega, de modo que un ROOT lo veía todo y el defecto quedaba invisible justo
    para quien podía detectarlo. Lo único que se propaga es lo que identifica al
    llamante; qué puede hacer se resuelve contra `/auth/me` con su token.
    """
    token = mcp_app.set_context(
        authorization=_header(scope, b"authorization"),
        tenant=_header(scope, b"x-tenant-slug"),
    )
    try:
        await _session_manager.handle_request(scope, receive, send)
    finally:
        mcp_app.reset_context(token)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Sin clave de firma no se arranca. Una clave vacía produce firmas que
    # cualquiera reproduce: el gate de escritura seguiría ahí, con su tarjeta y
    # su token, y no protegería nada — un control decorativo es peor que ninguno,
    # porque nadie lo revisa. Mismo criterio que los secretos con valor de ejemplo
    # del bloque 11: default vacío MÁS comprobación al arrancar; con una sola de
    # las dos mitades, el fallo vuelve a ser silencioso.
    common_secrets.require_secrets(
        {"MCP_PROPOSAL_SECRET": settings.mcp_proposal_secret},
        servicio="mcp-server",
    )

    # Cliente HTTP hacia el api-gateway (el MCP nunca toca BD/MinIO/Redis).
    app.state.gateway_client = httpx.AsyncClient(
        base_url=settings.gateway_url, timeout=settings.request_timeout
    )

    async with _session_manager.run():  # ciclo de vida del transporte MCP HTTP
        yield

    await app.state.gateway_client.aclose()


app = FastAPI(
    title="OrpycaMCP — MCP Server",
    description=(
        "Capa MCP: expone la API estable de OrpycaMCP como tools para agentes/LLM. "
        "Fachada fina cliente del api-gateway (ADR-019); sin lógica de negocio ni BD. "
        "Protocolo MCP por stdio (clientes locales) y HTTP streamable en /mcp."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Contexto entrante que las llamadas salientes reenvían (AREA-11-04/-08): el
# `X-Request-Id` del gateway queda en un ContextVar (traza por petición) y la IP
# del cliente real, solo si el gateway la firmó con la identidad.
app.add_middleware(ContextoEntranteMiddleware)
app.add_middleware(request_log.RequestLogMiddleware, servicio="mcp-server")

app.include_router(mcp_router)  # API HTTP simple del catálogo (/api/v1/mcp/...)
app.include_router(health_router)
# Protocolo MCP streamable (clientes remotos), montado en DOS rutas:
#
# - `/mcp` — acceso directo al servicio dentro de la red Docker (compatibilidad
#   con lo que ya existía y con el despliegue local por stdio/túnel).
# - `/api/v1/mcp/stream` — la MISMA aplicación bajo el prefijo que el gateway sí
#   enruta. Es lo que permite que los clientes MCP externos usen el mismo catálogo
#   y el mismo gate sin abrirle al proxy una forma de ruta distinta de
#   `/api/v1/...`: el cliente externo apunta aquí con
#   su bearer y hereda la validación JWT y la inyección de cabeceras del gateway,
#   en vez de hablar con el servicio a sus espaldas.
#
# El transporte es `stateless=True, json_response=True`, así que cada POST es una
# petición-respuesta corriente y el proxy del gateway la reenvía sin más. Con
# sesiones o SSE haría falta soporte de streaming en el proxy, y no lo hay.
app.mount("/mcp", _handle_mcp)
app.mount("/api/v1/mcp/stream", _handle_mcp)
