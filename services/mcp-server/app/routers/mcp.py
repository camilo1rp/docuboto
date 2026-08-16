# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Endpoints de la capa MCP (E18, ADR-019).

Transporte HTTP simplificado del catálogo de tools: listar tools visibles e
invocar una tool (que se traduce en una llamada al api-gateway). El binding del
SDK MCP (stdio / HTTP streamable, initialize/capabilities) envuelve este núcleo.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, Header, HTTPException, Request, status

from app.services import proposals, write_gate
from app.services.dispatcher import DispatchError, authorize, dispatch
from app.services.identity import IdentityError, resolve_identity
from app.tools.catalog import get_tool, visible_tools

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


def _perms(x_user_permissions: str | None) -> set[str]:
    if not x_user_permissions:
        return set()
    return {p.strip() for p in x_user_permissions.split(",") if p.strip()}


def _is_root(x_user_roles: str | None) -> bool:
    roles = {r.strip() for r in (x_user_roles or "").split(",")}
    return "ROOT" in roles


@router.get("/tools", summary="List MCP tools visible to the caller (RF-MCP-01)")
async def list_tools(
    x_user_permissions: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> dict[str, Any]:
    tools = visible_tools(_perms(x_user_permissions), is_root=_is_root(x_user_roles))
    return {"tools": [t.to_schema() for t in tools]}


@router.post("/proposals/execute", summary="Ejecuta una propuesta de escritura ya aprobada")
async def execute_proposal(
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    authorization: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
) -> dict[str, Any]:
    """El otro extremo del ciclo: `invoke` propone, esto ejecuta.

    El cuerpo lleva **solo el token**. Los argumentos salen del token firmado, no
    de aquí: si se aceptaran del cuerpo, la propuesta describiría un acto y se
    ejecutaría otro, que es justo lo que este diseño existe para impedir.
    """
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "propuesta_invalida", "detail": "falta el token", "status": 400},
        )

    client: httpx.AsyncClient = request.app.state.gateway_client
    try:
        identidad = await resolve_identity(
            client, authorization=authorization or "", tenant_slug=x_tenant_slug or ""
        )
        return await write_gate.ejecutar(
            client,
            token,
            identidad=identidad,
            authorization=authorization or "",
            tenant_slug=x_tenant_slug or "",
        )
    except IdentityError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.code, "status": exc.status},
        )
    except proposals.ProposalError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.detail, "status": exc.status},
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "gateway_unreachable", "detail": str(exc), "status": 502},
        )


@router.post("/tools/{name}/invoke", summary="Invoke an MCP tool (RF-MCP-03..08)")
async def invoke_tool(
    name: str,
    request: Request,
    args: dict[str, Any] = Body(default_factory=dict),
    authorization: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
    x_user_permissions: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> dict[str, Any]:
    """Invoca una tool. Las de ESCRITURA devuelven una propuesta, no un efecto.

    `X-Confirm-Write` ya no se lee. Era un booleano que cualquiera podía poner y
    que no ataba la confirmación a ningún acto concreto: confirmaba «algo», no
    «esto». Quien la envíe ahora recibirá una propuesta igualmente — el camino
    seguro es el que sale por defecto.
    """
    tool = get_tool(name)
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "tool_not_found", "detail": name, "status": 404},
        )
    client: httpx.AsyncClient = request.app.state.gateway_client
    try:
        identidad = await resolve_identity(
            client, authorization=authorization or "", tenant_slug=x_tenant_slug or ""
        )
    except IdentityError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.code, "status": exc.status},
        )

    try:
        authorize(
            tool,
            permissions=identidad.permissions,
            is_root=identidad.is_root,
            levels=identidad.levels,
        )
        if tool.write:
            # No se despacha: se propone. La ejecución vive en
            # `POST /proposals/execute`, y consume el token firmado.
            return write_gate.preparar(
                tool, args, sub=identidad.user_id, ten=identidad.tenant_slug
            )
        resp = await dispatch(
            client, tool, args, authorization=authorization, tenant_slug=x_tenant_slug
        )
    except DispatchError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.code, "status": exc.status},
        )
    except proposals.ProposalError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.detail, "status": exc.status},
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "gateway_unreachable", "detail": str(exc), "status": 502},
        )

    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}
    return {"tool": name, "status": resp.status_code, "data": data}
