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
from app.services import dispatcher, proposals, write_gate
from app.services.identity import IdentityError, resolve_identity
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


# La tool genérica de confirmación, para clientes MCP EXTERNOS.
#
# El asistente web tiene su tarjeta y su endpoint (`/api/v1/assistant/confirmar`).
# Un cliente MCP de terceros no tiene ninguno de los dos, así que sin esto vería
# la propuesta y no tendría manera de llevarla a cabo: el gate habría dejado de
# ser una confirmación para pasar a ser un muro.
#
# Va aquí y no en `CATALOG` porque no corresponde a ningún endpoint del gateway:
# el catálogo declara traducciones tool → API, y esto es protocolo. Meterlo allí
# habría obligado a inventarle una ruta falsa.
CONFIRMAR = "confirmar_accion"

_CONFIRMAR_SCHEMA = {
    "type": "object",
    "properties": {
        "token": {
            "type": "string",
            "description": (
                "El `proposal.token` que devolvió la invocación de la escritura. "
                "Preséntalo tal cual; no lo modifiques ni lo construyas."
            ),
        }
    },
    "required": ["token"],
    "additionalProperties": False,
}


def _annotations(t: catalog.Tool) -> types.ToolAnnotations:
    """Pistas MCP estándar para que el cliente ponga SU propia fricción.

    Son pistas, no controles: un cliente puede ignorarlas enteras y por eso la
    garantía sigue viviendo en la propuesta firmada, no aquí. Se emiten porque un
    cliente que sí las honra pregunta antes de radicar, y eso vale — pero
    confundir la pista con el control sería delegar la seguridad en software
    ajeno, que es exactamente lo que hacía la auto-confirmación que este bloque
    retiró.
    """
    return types.ToolAnnotations(
        title=t.operacion or t.name,
        readOnlyHint=not t.write,
        # `destructive` es `None` solo en las de lectura, donde el hint no aplica.
        destructiveHint=bool(t.destructive),
        idempotentHint=not t.write,
        openWorldHint=False,
    )


def build_tools() -> list[types.Tool]:
    s = _session()
    visibles = catalog.visible_tools(s["permissions"], is_root=s["is_root"])
    tools = [
        types.Tool(
            name=t.name,
            description=t.description + (" [escritura]" if t.write else ""),
            inputSchema=catalog.json_schema(t),
            annotations=_annotations(t),
        )
        for t in visibles
    ]
    # Solo se ofrece si hay algo que confirmar: a quien no alcanza ninguna
    # escritura, una tool de confirmar le sobra y le sugiere un poder que no
    # tiene.
    if any(t.write for t in visibles):
        tools.append(
            types.Tool(
                name=CONFIRMAR,
                description=(
                    "Ejecuta una propuesta de escritura previamente emitida. "
                    "IMPORTANTE: solo debes llamarla después de que una PERSONA haya "
                    "leído los datos de la propuesta y los haya aprobado. El acto que "
                    "se ejecuta es el que va firmado en el token, no el que describas aquí."
                ),
                inputSchema=_CONFIRMAR_SCHEMA,
                annotations=types.ToolAnnotations(
                    title="Confirmar una acción propuesta",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        )
    return tools


async def _authorization(s: dict, client: httpx.AsyncClient) -> str:
    """HTTP: el bearer llega por cabecera. stdio: lo gestiona el TokenManager."""
    if s.get("from_header"):
        return s["authorization"] or ""
    from app.services.oauth import token_manager

    return await token_manager.authorization(client) or ""


_ELICIT_SCHEMA = {
    "type": "object",
    "properties": {
        "confirmar": {
            "type": "boolean",
            "title": "¿Ejecutar esta acción?",
            "description": "Marque solo si los datos de arriba son correctos.",
        }
    },
    "required": ["confirmar"],
}


async def _elicitar(propuesta: dict) -> bool | None:
    """Pregunta a la persona en banda, si el transporte tiene canal de vuelta.

    Devuelve `True` (aprobó), `False` (rechazó) o `None` (no se pudo preguntar,
    y entonces queda el camino de dos tools).

    **Por qué esto no debilita nada.** La aprobación por elicitación no ejecuta
    de una manera distinta: sigue pasando por `write_gate.ejecutar` con el mismo
    token firmado, así que se reautoriza y se consume igual. Lo único que cambia
    es *quién pinta la pregunta*. Si esto ejecutara por su cuenta, sería la
    auto-confirmación que este bloque acaba de retirar, con otro nombre.

    En el HTTP streamable actual (`stateless=True`) no hay sesión que preguntar y
    devuelve `None` — límite declarado, no disimulado.

    El texto lo redacta el CATÁLOGO (`campos`), no el modelo: un texto generado
    podría describir mal el acto que se está aprobando.
    """
    try:
        ctx = server.request_context
        sesion = ctx.session
    except (LookupError, AttributeError):
        return None
    if not sesion.check_client_capability(types.ClientCapabilities(elicitation={})):
        return None

    p = propuesta["proposal"]
    detalle = "\n".join(f"- {c['etiqueta']}: {c['valor']}" for c in p["campos"])
    try:
        resultado = await sesion.elicit(
            message=f"{p['operacion']}\n\n{detalle}",
            requestedSchema=_ELICIT_SCHEMA,
        )
    except Exception:
        # Cliente que anuncia la capacidad y no la sirve, o se cayó la sesión. Se
        # cae al camino de dos tools en vez de dar el acto por aprobado: ante la
        # duda, no se ejecuta.
        return None

    if resultado.action != "accept":
        return False
    return bool((resultado.content or {}).get("confirmar"))


async def call(name: str, arguments: dict, client: httpx.AsyncClient) -> str:
    s = _session()
    authorization = await _authorization(s, client)

    if name == CONFIRMAR:
        token = arguments.get("token")
        if not isinstance(token, str) or not token:
            raise dispatcher.DispatchError("propuesta_invalida", 400)
        identidad = await resolve_identity(
            client, authorization=authorization, tenant_slug=s["tenant"] or ""
        )
        return json.dumps(
            await write_gate.ejecutar(
                client,
                token,
                identidad=identidad,
                authorization=authorization,
                tenant_slug=identidad.tenant_slug,
            ),
            ensure_ascii=False,
        )

    tool = catalog.get_tool(name)
    if tool is None:
        raise dispatcher.DispatchError("tool_not_found", 404)
    # El binding ya NO auto-confirma. Antes daba la escritura por aprobada
    # «porque el human-in-the-loop lo aporta el cliente MCP», y eso convertía la
    # garantía en una suposición sobre software ajeno: un cliente que no
    # preguntara nada ejecutaba igual. Ahora una escritura devuelve la propuesta
    # firmada y la ejecución exige una segunda llamada con ese token.
    dispatcher.authorize(tool, permissions=s["permissions"], is_root=s["is_root"])
    if tool.write:
        # La identidad de la propuesta se resuelve contra `/auth/me`, NO contra el
        # diccionario de sesión. La primera versión firmaba con `s.get("sub")`,
        # clave que nunca existió en ese diccionario: emitía tokens con `sub=""`
        # que la verificación rechazaba después como ajenos. Salía una propuesta
        # de aspecto impecable e inconfirmable — un fallo que solo aparece al
        # pulsar el botón, no al emitirla.
        identidad = await resolve_identity(
            client, authorization=authorization, tenant_slug=s["tenant"] or ""
        )
        propuesta = write_gate.preparar(
            tool, arguments, sub=identidad.user_id, ten=identidad.tenant_slug
        )
        elegida = await _elicitar(propuesta)
        if elegida is False:
            return json.dumps(
                {
                    "kind": "cancelled",
                    "ejecutado": False,
                    "aviso": "La persona no aprobó la acción. No se ejecutó nada.",
                },
                ensure_ascii=False,
            )
        if elegida is True:
            return json.dumps(
                await write_gate.ejecutar(
                    client,
                    propuesta["proposal"]["token"],
                    identidad=identidad,
                    authorization=authorization,
                    tenant_slug=identidad.tenant_slug,
                ),
                ensure_ascii=False,
            )
        return json.dumps(propuesta, ensure_ascii=False)

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
        except proposals.ProposalError as exc:
            # Propuesta caducada, ajena, reusada o con la firma tocada. Se
            # devuelve como resultado de la tool y no como excepción: el cliente
            # tiene que poder decirle a la persona *por qué* no se ejecutó, y una
            # sesión rota no dice nada.
            text = json.dumps({"error": exc.code, "detail": exc.detail, "status": exc.status})
        except IdentityError as exc:
            text = json.dumps({"error": exc.code, "status": exc.status})
        except httpx.RequestError as exc:
            text = json.dumps({"error": "gateway_unreachable", "detail": str(exc)})
    return [types.TextContent(type="text", text=text)]


# Registra los handlers de resources y prompts sobre `server` (al importar mcp_app,
# ambos transportes —stdio y HTTP— los exponen). Import al final para evitar ciclos.
from app import mcp_resources as _mcp_resources  # noqa: E402,F401
