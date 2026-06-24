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

from app import mcp_app
from app.core.config import settings
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
    """Endpoint MCP streamable: inyecta el contexto de sesión desde las cabeceras."""
    perms = _header(scope, b"x-user-permissions") or ""
    roles = _header(scope, b"x-user-roles") or ""
    token = mcp_app.set_context(
        authorization=_header(scope, b"authorization"),
        tenant=_header(scope, b"x-tenant-slug"),
        permissions={p.strip() for p in perms.split(",") if p.strip()},
        is_root="ROOT" in {r.strip() for r in roles.split(",")},
    )
    try:
        await _session_manager.handle_request(scope, receive, send)
    finally:
        mcp_app.reset_context(token)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
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

app.include_router(mcp_router)  # API HTTP simple del catálogo (/api/v1/mcp/...)
app.include_router(health_router)
# Protocolo MCP streamable (clientes remotos).
app.mount("/mcp", _handle_mcp)
