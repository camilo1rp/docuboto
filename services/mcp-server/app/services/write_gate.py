# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
El gate de escritura, uno solo para los tres transportes.

Antes había tres comportamientos distintos según por dónde entraras: el router
HTTP exigía una cabecera, el binding MCP la daba por puesta, y el asistente
prohibía toda escritura. Tres respuestas para la misma pregunta es garantía de
que alguna está mal, y la que estaba mal era la que menos se miraba.

Ahora hay uno: **invocar una escritura devuelve una propuesta y no ejecuta
nada**. Que devuelva una propuesta en vez de un error es deliberado — un cliente
antiguo u hostil que fabrique cualquier cabecera obtiene una descripción del
acto, no un efecto. El camino seguro es el que sale por defecto, no el que hay
que acordarse de proteger.

**La tarjeta la redacta el catálogo, no el modelo.** Un texto generado podría
describir mal el acto que se está firmando, y eso sería ingeniería social sobre
la propia persona que confirma.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services import dispatcher, proposals
from app.tools.catalog import ArgError, Tool, card_fields, coerce_args, get_tool


def secretos_vigentes() -> tuple[str, ...]:
    """Clave actual y anterior: rotar no invalida las propuestas ya emitidas."""
    return tuple(
        s for s in (settings.mcp_proposal_secret, settings.mcp_proposal_secret_previous) if s
    )


async def ejecutar(
    client: Any,
    token: str,
    *,
    identidad: Any,
    authorization: str,
    tenant_slug: str,
) -> dict[str, Any]:
    """Consume una propuesta y ejecuta el acto que lleva firmado dentro.

    Orden deliberado: verificar -> **reautorizar** -> consumir -> despachar.

    La reautorización no es redundante. Entre proponer y confirmar median los
    segundos que tarda una persona en leer la tarjeta, y en ese hueco le pueden
    haber retirado el permiso. La propuesta acredita *qué* se aprobó, no que
    quien la presenta siga pudiendo hacerlo — confundir esas dos cosas es lo que
    convierte un token en un salvoconducto.

    Y el consumo va ANTES del despacho: si se marcara después, un fallo de red
    dejaría la propuesta viva y el reintento podría duplicar el acto.
    """
    propuesta = proposals.verify(
        token,
        secretos=secretos_vigentes(),
        sub=identidad.user_id,
        ten=identidad.tenant_slug,
    )

    tool = get_tool(propuesta.tool)
    if tool is None:
        # La tool desapareció del catálogo entre proponer y ejecutar (un
        # despliegue a mitad). Mejor negarse que despachar a ciegas.
        raise proposals.ProposalError(
            "tool_not_found", 404, "la acción ya no está disponible"
        )

    try:
        dispatcher.authorize(
            tool,
            permissions=identidad.permissions,
            is_root=identidad.is_root,
            levels=identidad.levels,
        )
    except dispatcher.DispatchError as exc:
        raise proposals.ProposalError(
            exc.code, exc.status, "ya no tiene permiso para ejecutar esta acción"
        ) from exc

    proposals.consume(propuesta)

    resp = await dispatcher.dispatch(
        client,
        tool,
        propuesta.args,  # del TOKEN, nunca del cuerpo de esta petición
        authorization=authorization,
        tenant_slug=tenant_slug,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}

    return {
        "kind": "result",
        "ejecutado": True,
        "proposal_id": propuesta.jti,
        "tool": tool.name,
        "operacion": tool.operacion or tool.name,
        "status": resp.status_code,
        "data": data,
    }


def preparar(tool: Tool, args: dict[str, Any], *, sub: str, ten: str) -> dict[str, Any]:
    """Valida el acto, lo firma y devuelve la tarjeta que se mostrará.

    Los argumentos se validan **aquí**, en la fase de propuesta, para que lo que
    se firma sea exactamente lo que se ejecutará: la ejecución no vuelve a
    aceptar argumentos del cliente, los saca del token.
    """
    try:
        limpio = coerce_args(tool, dict(args))
    except ArgError as exc:
        raise proposals.ProposalError(exc.code, 400, exc.detail) from exc

    token, propuesta = proposals.issue(
        tool.name,
        limpio,
        sub=sub,
        ten=ten,
        secreto=settings.mcp_proposal_secret,
        ttl_seconds=settings.mcp_proposal_ttl_seconds,
    )

    return {
        "kind": "proposal",
        "ejecutado": False,
        "proposal": {
            "id": propuesta.jti,
            "tool": tool.name,
            "operacion": tool.operacion or tool.name,
            "campos": card_fields(tool, limpio),
            "expira_en": propuesta.exp,
            "token": token,
        },
        # Para un cliente MCP sin interfaz propia, dicho en el idioma del
        # protocolo: si no lee el `kind`, que al menos lea esto.
        "aviso": (
            "NO SE HA EJECUTADO NADA. Esta es una propuesta: para llevarla a cabo, "
            "una persona debe aprobarla y confirmarla explícitamente."
        ),
    }
