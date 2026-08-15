# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Servidor MCP de OrpycaMCP (E18, ADR-019) — binding del SDK oficial.

Envuelve el catálogo de tools declarativo y el dispatcher (cliente del gateway)
con el protocolo MCP, en dos transportes:
- **stdio** (clientes locales): el contexto de sesión (token/tenant/permisos) llega
  por variables de entorno (settings).
- **HTTP streamable** (clientes remotos): el contexto llega por cabeceras HTTP de
  cada petición y se inyecta vía un ContextVar.

La autenticación, el RBAC y el aislamiento por tenant los resuelve la BD vía el
gateway: aquí solo se propaga `Authorization` + `X-Tenant-Slug` y se filtra la
visibilidad/scope de las tools por los permisos del usuario.
"""

from __future__ import annotations

import json
from contextvars import ContextVar

import httpx
import mcp.types as types
from mcp.server import Server

from app.core.config import settings
from app.services import dispatcher
from app.tools import catalog

server: Server = Server("orpycamcp")

# Contexto de sesión por petición (HTTP streamable). Si está vacío, se usa el
# entorno (stdio). Estructura: {authorization, tenant, permissions:set, is_root:bool}.
_ctx: ContextVar[dict | None] = ContextVar("mcp_session_ctx", default=None)


def set_context(
    *, authorization: str | None, tenant: str | None, permissions: set[str], is_root: bool
):
    """Fija el contexto de sesión para la petición HTTP actual. Devuelve el token de reset."""
    return _ctx.set(
        {
            "authorization": authorization,
            "tenant": tenant,
            "permissions": permissions,
            "is_root": is_root,
            "from_header": True,  # HTTP: el cliente ya aporta su bearer
        }
    )


def reset_context(token) -> None:
    _ctx.reset(token)


def _session() -> dict:
    ctx = _ctx.get()
    if ctx is not None:
        return ctx
    # Fallback stdio: contexto desde el entorno.
    perms = {p.strip() for p in settings.mcp_user_permissions.split(",") if p.strip()}
    roles = {r.strip() for r in settings.mcp_user_roles.split(",")}
    return {
        "authorization": None,  # stdio: lo resuelve el TokenManager (OAuth por sesión)
        "tenant": settings.mcp_tenant_slug or None,
        "permissions": perms,
        "is_root": "ROOT" in roles,
        "from_header": False,
    }


# `_input_schema` se eliminó: declaraba solo los parámetros de RUTA como cadenas
# y dejaba `additionalProperties: True`, de modo que el cliente MCP veía un
# contrato distinto —y mucho más laxo— del que veía el modelo del asistente. Los
# dos consumen ahora `catalog.json_schema`, que es la única declaración.


def build_tools() -> list[types.Tool]:
    s = _session()
    return [
        types.Tool(
            name=t.name,
            description=t.description + (" [escritura]" if t.write else ""),
            inputSchema=catalog.json_schema(t),
        )
        for t in catalog.visible_tools(s["permissions"], is_root=s["is_root"])
    ]


async def call(name: str, arguments: dict, client: httpx.AsyncClient) -> str:
    s = _session()
    tool = catalog.get_tool(name)
    if tool is None:
        raise dispatcher.DispatchError("tool_not_found", 404)
    # En MCP la aprobación de la operación la da el cliente/agente (human-in-the-loop):
    # el gate de escritura se da por confirmado; el scope por permiso sí se aplica.
    dispatcher.authorize(
        tool, permissions=s["permissions"], is_root=s["is_root"], confirm_write=True
    )
    # HTTP: el bearer llega por cabecera. stdio: lo gestiona el TokenManager (OAuth).
    if s.get("from_header"):
        authorization = s["authorization"]
    else:
        from app.services.oauth import token_manager

        authorization = await token_manager.authorization(client)
    resp = await dispatcher.dispatch(
        client,
        tool,
        arguments,
        authorization=authorization,
        tenant_slug=s["tenant"],
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}
    return json.dumps({"status": resp.status_code, "data": data}, ensure_ascii=False)


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return build_tools()


@server.call_tool()
async def _call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    async with httpx.AsyncClient(
        base_url=settings.gateway_url, timeout=settings.request_timeout
    ) as client:
        try:
            text = await call(name, arguments or {}, client)
        except dispatcher.DispatchError as exc:
            text = json.dumps({"error": exc.code, "status": exc.status})
        except httpx.RequestError as exc:
            text = json.dumps({"error": "gateway_unreachable", "detail": str(exc)})
    return [types.TextContent(type="text", text=text)]


# Registra los handlers de resources y prompts sobre `server` (al importar mcp_app,
# ambos transportes —stdio y HTTP— los exponen). Import al final para evitar ciclos.
from app import mcp_resources as _mcp_resources  # noqa: E402,F401
