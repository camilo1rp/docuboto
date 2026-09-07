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
from app.services import dispatcher, procedure_runner, proposals
from app.tools.catalog import ArgError, Invocable, card_fields, coerce_args, get_tool
from app.tools.procedures import Procedimiento, get_procedimiento

# Frase que hay que TECLEAR para llevar a cabo un acto irreversible. Un clic no
# basta cuando el acto no se deshace — es la convención que el proyecto ya usa
# para la reasignación en cascada (`REASIGNAR`) y para la rotación de claves.
#
# Se comprueba **aquí**, no en la interfaz, y el acto se identifica por el TOKEN
# FIRMADO: el cliente no puede mentir sobre si lo que confirma es irreversible,
# porque no es él quien lo dice. Una frase validada solo en el navegador es
# teatro: el navegador es la parte hostil.
#
# Límite dicho en voz alta: por un cliente MCP externo, quien teclea la frase es
# el modelo, no una persona, así que ahí NO es un control humano — el control
# humano lo aporta el flujo de aprobación del propio cliente. Se exige igual
# para no tener dos gates distintos según por dónde entres, que es de donde
# salió todo esto.
FRASE_IRREVERSIBLE = "CONFIRMO"


def frase_requerida(tool: Invocable, args: dict[str, Any]) -> str | None:
    """Qué hay que teclear para llevar a cabo este acto — o `None` si basta aprobar.

    Tres niveles, y el salto entre el segundo y el tercero es el que importa:

    * lectura o escritura reversible → nada que teclear;
    * irreversible → la frase fija `CONFIRMO`;
    * irreversible **sobre un objeto concreto que hay que identificar** →
      el valor del parámetro que la tool declare en `confirmar_con`.

    El tercero existe porque una frase fija se teclea de memoria antes de leer:
    confirma «un acto», no «este acto». Reproducir el identificador del lote no
    se puede anticipar — hay que haber mirado la tarjeta.

    Si el parámetro no viene en los argumentos se cae a la frase fija en vez de
    quedarse sin exigir nada: degradar hacia MÁS control y no hacia menos.
    """
    if tool.confirmar_con:
        valor = args.get(tool.confirmar_con)
        if valor not in (None, ""):
            return str(valor)
    return FRASE_IRREVERSIBLE if tool.destructive else None


def frase_valida(frase: Any, esperada: str | None = None) -> bool:
    """¿La frase tecleada es la que este acto exige?

    La comparación es sin distinguir mayúsculas y sin espacios de sobra, y NO
    porque dé igual: exigir una capitalización exacta convierte el control en un
    acertijo de tecleo, y quien se pelea tres veces con un identificador termina
    copiando y pegando sin leer — que es justo lo que esto quiere evitar.
    """
    objetivo = (esperada or FRASE_IRREVERSIBLE).strip().upper()
    return isinstance(frase, str) and frase.strip().upper() == objetivo


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
    frase: Any = None,
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

    # Un procedimiento se propone y se confirma EXACTAMENTE igual que una tool:
    # una sola tarjeta para el acto completo. Pedir una confirmación por paso
    # enseñaría a confirmar sin leer, que es peor que no preguntar.
    tool = get_tool(propuesta.tool) or get_procedimiento(propuesta.tool)
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

    # Qué acto es lo dice el TOKEN, no el cliente: `tool` sale de la propuesta
    # verificada. Por eso nadie puede saltarse esta puerta declarando que su
    # acto no era irreversible.
    # Qué frase exige lo dice el catálogo sobre los argumentos DEL TOKEN, no el
    # cliente: nadie puede rebajar su propio control declarando que su acto era
    # de los sencillos.
    exigida = frase_requerida(tool, propuesta.args)
    if exigida and not frase_valida(frase, exigida):
        raise proposals.ProposalError(
            "confirmacion_requerida",
            400,
            f"Esta acción no se deshace. Para llevarla a cabo, escriba «{exigida}».",
        )

    proposals.consume(propuesta)

    if isinstance(tool, Procedimiento):
        parte = await procedure_runner.ejecutar(
            client,
            tool,
            propuesta.args,  # del TOKEN, nunca del cuerpo de esta petición
            authorization=authorization,
            tenant_slug=tenant_slug,
            permissions=identidad.permissions,
            is_root=identidad.is_root,
            levels=identidad.levels,
        )
        return {
            "kind": "result",
            # `ejecutado` dice que el procedimiento CORRIÓ, y `completado` (dentro
            # del parte) si llegó al final. Son dos cosas distintas y fundirlas
            # haría que un trámite cortado a mitad se leyera como no ocurrido —
            # justo lo que lleva a repetirlo y duplicar un radicado.
            "ejecutado": True,
            "proposal_id": propuesta.jti,
            "tool": tool.name,
            "operacion": tool.operacion or tool.name,
            "status": 200 if parte["completado"] else 409,
            "data": parte,
        }

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


def preparar(tool: Invocable, args: dict[str, Any], *, sub: str, ten: str) -> dict[str, Any]:
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
            # Para que la tarjeta lo AVISE antes, no después. El backend lo exige
            # igualmente; esto es lo que evita que la persona descubra la frase
            # al recibir un error, que es la misma forma de límite-descubierto-
            # fallando que este trabajo persigue.
            "irreversible": bool(tool.destructive),
            "frase_requerida": frase_requerida(tool, limpio),
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
