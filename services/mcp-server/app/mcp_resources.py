# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Resources y prompts MCP de OrpycaMCP (E18, ADR-019).

- **Resources**: datos consultables por URI `orfeo://…`. Los dinámicos (radicado,
  expediente, índice) se resuelven contra el gateway reutilizando el catálogo de
  tools; el recurso estático `orfeo://normativa` expone el marco normativo.
- **Prompts**: plantillas reutilizables (radicar PQRSD, resumen de expediente,
  buscar antecedentes) que orientan al agente a usar las tools.

Registrados sobre el mismo `server` que el binding de tools, por lo que están
disponibles en ambos transportes (stdio y HTTP streamable).
"""

from __future__ import annotations

import httpx
import mcp.types as types

from app.core.config import settings
from app.mcp_app import call, server

_NORMATIVA = (
    "Marco normativo del SGDEA (Colombia): Ley 594/2000 (Ley General de Archivos), "
    "Acuerdo AGN 060/2001 (comunicaciones oficiales), Acuerdo AGN 042/2002 (FUID), "
    "Acuerdo AGN 001/2024 (expediente electrónico, índice firmado, transferencias), "
    "Ley 527/1999 y Decreto 2364/2012 (firma electrónica), Ley 1755/2015 (PQRS), "
    "Decreto 2609/2012 e ISO 14721 OAIS (preservación digital)."
)

# URI dinámica → (tool del catálogo, nombre del parámetro de ruta).
_URI_MAP = {
    "radicado": ("consultar_radicado", "document_id"),
    "expediente": ("consultar_expediente", "expediente_id"),
}


@server.list_resources()
async def _list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri="orfeo://normativa",
            name="Marco normativo",
            description="Normas archivísticas colombianas aplicables al SGDEA.",
            mimeType="text/plain",
        )
    ]


@server.list_resource_templates()
async def _list_resource_templates() -> list[types.ResourceTemplate]:
    return [
        types.ResourceTemplate(
            uriTemplate="orfeo://radicado/{id}",
            name="Radicado",
            description="Consulta un radicado por su id.",
            mimeType="application/json",
        ),
        types.ResourceTemplate(
            uriTemplate="orfeo://expediente/{id}",
            name="Expediente",
            description="Consulta un expediente por su id.",
            mimeType="application/json",
        ),
        types.ResourceTemplate(
            uriTemplate="orfeo://expediente/{id}/indice",
            name="Índice del expediente",
            description="Índice electrónico vigente de un expediente.",
            mimeType="application/json",
        ),
    ]


@server.read_resource()
async def _read_resource(uri) -> str:
    u = str(uri)
    if u == "orfeo://normativa":
        return _NORMATIVA
    rest = u.removeprefix("orfeo://")
    parts = rest.split("/")
    async with httpx.AsyncClient(
        base_url=settings.gateway_url, timeout=settings.request_timeout
    ) as client:
        # orfeo://expediente/{id}/indice
        if len(parts) == 3 and parts[0] == "expediente" and parts[2] == "indice":
            return await call("indice_expediente", {"expediente_id": parts[1]}, client)
        # orfeo://radicado/{id} | orfeo://expediente/{id}
        if len(parts) == 2 and parts[0] in _URI_MAP:
            tool, param = _URI_MAP[parts[0]]
            return await call(tool, {param: parts[1]}, client)
    raise ValueError(f"recurso no soportado: {u}")


# --- Prompts ---------------------------------------------------------

_PROMPTS = {
    "radicar_pqrsd": types.Prompt(
        name="radicar_pqrsd",
        description="Guía para radicar una PQRSD ciudadana.",
        arguments=[
            types.PromptArgument(name="asunto", description="Asunto breve", required=True),
            types.PromptArgument(
                name="descripcion", description="Detalle de la petición", required=True
            ),
        ],
    ),
    "resumen_expediente": types.Prompt(
        name="resumen_expediente",
        description="Resume un expediente usando su índice y radicados.",
        arguments=[
            types.PromptArgument(
                name="expediente_id", description="Id del expediente", required=True
            ),
        ],
    ),
    "buscar_antecedentes": types.Prompt(
        name="buscar_antecedentes",
        description="Busca antecedentes documentales sobre un tema.",
        arguments=[
            types.PromptArgument(name="tema", description="Tema o palabras clave", required=True),
        ],
    ),
}


@server.list_prompts()
async def _list_prompts() -> list[types.Prompt]:
    return list(_PROMPTS.values())


def _msg(text: str) -> types.PromptMessage:
    return types.PromptMessage(role="user", content=types.TextContent(type="text", text=text))


@server.get_prompt()
async def _get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    a = arguments or {}
    if name == "radicar_pqrsd":
        text = (
            "Radica una PQRSD con la tool `radicar_documento` (doc_type='E'). "
            f"Asunto: {a.get('asunto','')}. Descripción: {a.get('descripcion','')}. "
            "Devuelve el número de radicado al ciudadano."
        )
    elif name == "resumen_expediente":
        text = (
            f"Resume el expediente {a.get('expediente_id','')}: consulta el recurso "
            f"`orfeo://expediente/{a.get('expediente_id','')}` y su índice "
            f"`orfeo://expediente/{a.get('expediente_id','')}/indice`, y sintetiza estado, "
            "serie TRD, documentos y disposición."
        )
    elif name == "buscar_antecedentes":
        text = (
            f"Busca antecedentes sobre «{a.get('tema','')}» con la tool `buscar_radicados` "
            "y, si aplica, `consultar_expediente`; resume los hallazgos con sus números de radicado."
        )
    else:
        raise ValueError(f"prompt desconocido: {name}")
    return types.GetPromptResult(
        description=_PROMPTS[name].description if name in _PROMPTS else name,
        messages=[_msg(text)],
    )
