# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Dispatcher de tools MCP (E18, ADR-019).

Traduce la invocación de una tool a una llamada HTTP al api-gateway, propagando el
token Keycloak del usuario y el tenant. No contiene lógica de negocio: compone
contratos REST existentes. La autorización real la resuelve la BD vía gateway
(ADR-013); aquí solo se filtra la visibilidad/gate por permisos del usuario.
"""

from __future__ import annotations

import httpx

from app.tools.catalog import Tool


class DispatchError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _forward_headers(authorization: str | None, tenant_slug: str | None) -> dict:
    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    if tenant_slug:
        headers["X-Tenant-Slug"] = tenant_slug
    return headers


def authorize(tool: Tool, *, permissions: set[str], is_root: bool, confirm_write: bool) -> None:
    """Aplica scope por permiso y gate de escritura antes de invocar."""
    if tool.permission and not is_root and tool.permission not in permissions:
        raise DispatchError("permiso_insuficiente", 403)
    if tool.write and not confirm_write:
        raise DispatchError("confirmacion_requerida", 412)


async def dispatch(
    client: httpx.AsyncClient,
    tool: Tool,
    args: dict,
    *,
    authorization: str | None,
    tenant_slug: str | None,
) -> httpx.Response:
    args = dict(args)
    # Rellena los parámetros de ruta; el resto va a query (GET) o body (escritura).
    path = tool.path
    for p in tool.path_params:
        if p not in args:
            raise DispatchError(f"falta_parametro:{p}", 400)
        path = path.replace(f"{{{p}}}", str(args.pop(p)))

    headers = _forward_headers(authorization, tenant_slug)
    if tool.method == "GET":
        return await client.get(path, params=args, headers=headers)
    return await client.post(path, json=args, headers=headers)
