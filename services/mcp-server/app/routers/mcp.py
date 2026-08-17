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


@router.get("/tools", summary="List MCP tools visible to the caller (RF-MCP-01)")
async def list_tools(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
) -> dict[str, Any]:
    """El catálogo que ESTE llamante puede usar, resuelto contra `/auth/me`.

    Antes se componía a partir de `X-User-Permissions`, una cabecera que **nadie
    produce**: el gateway inyecta `X-User-Id`, `X-Username`, `X-Tenant-Slug`,
    `X-User-Roles`, `X-Canal` y `X-Real-Ip`, y descarta esta si la manda un
    cliente (bien: sería forjable). Llegaba siempre vacía, así que este endpoint
    —el que existe precisamente para decirle a alguien qué puede hacer— le
    contestaba a todo el mundo con las tools sin permiso y nada más.
    Y `X-User-Roles` sí llega, de modo que un ROOT lo veía todo: el defecto
    quedaba escondido justo para quien podía detectarlo.
    """
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
    tools = visible_tools(
        identidad.permissions, is_root=identidad.is_root, levels=identidad.levels
    )
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
) -> dict[str, Any]:
    """Invoca una tool. Las de ESCRITURA devuelven una propuesta, no un efecto.

    Ya no declara `X-User-Permissions` ni `X-User-Roles`: los leía como
    parámetros y no los usaba para nada —la autorización sale de `/auth/me`
    desde el bloque 0—, pero seguían apareciendo en el OpenAPI como si fueran
    parte del contrato. Una cabecera documentada que nadie honra es una
    invitación a que el siguiente la use creyendo que sirve.

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
