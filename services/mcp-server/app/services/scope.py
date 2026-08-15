# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Marco de alcance: qué puede hacer esta persona, y por dónde sí cuando no puede.

**Por qué existe.** Los permisos venían funcionando como un filtro silencioso:
`visible_tools` escondía lo que el usuario no podía usar, de modo que el límite se
descubría **fallando**. Alguien pide «envía este documento por correo», el
asistente lo intenta, y aparece un 403 — o, peor, la persona se queda esperando
una acción que con sus credenciales nunca iba a ocurrir. Eso hace perder el tiempo
a dos personas y convierte un límite legítimo en una avería aparente.

El marco invierte el orden: se declara **antes** de empezar, gobierna el curso del
trabajo, y cuando algo no está permitido ofrece **la vía que sí existe** —«no
puede enviar, pero puede trasladar el radicado a correspondencia para que lo
envíen»—. Esa vía alternativa es la parte útil: negar sin más deja a la persona
igual de atascada.

**Se DERIVA, no se duplica.** Sale del catálogo —que ya declara permiso, nivel,
operación de dominio y vía alternativa por tool— cruzado con `/auth/me`,
`/auth/clearance` y `/auth/context`. Añadir una tool actualiza el marco sola. Una
segunda tabla que enumerase capacidades a mano caducaría en silencio, que es
exactamente el defecto del que salió todo esto.

**Relación con ADR-019.** Esto no es lógica de dominio ni un endpoint nuevo de
negocio: es introspección del propio servidor MCP sobre lo que ofrece y por qué,
igual que `GET /api/v1/mcp/tools`. No toca BD ni añade reglas: compone tres
lecturas existentes.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.identity import Identity
from app.tools import catalog
from app.tools.catalog import Tool

# `security_levels` (auth-service/migrations/tenant/003_classification.sql).
_NIVELES = {1: "Pública", 2: "Reservada", 3: "Clasificada"}

# Lo que el sistema NO distingue hoy, dicho en voz alta.
#
# Sin esto el marco caería en la trampa que persigue: prometer una precisión que
# el sistema no tiene. Se verificó en el código, no se supuso.
#
# - `PERM_RADI_ENTRADA` y `PERM_RADI_INTERNO` existen en el catálogo de permisos y
#   NINGÚN gate los usa: los tres tipos de radicado exigen `PERM_RADI`. Decir
#   «puede radicar solo entradas» sería falso.
# - No hay filtro por dependencia en las lecturas de radicados; el acotamiento
#   real es por nivel de seguridad. Decir «solo ve lo de su dependencia» también
#   sería falso.
LIMITES_DECLARADOS = (
    "El permiso de radicación no distingue el tipo de documento: quien puede "
    "radicar, puede radicar entradas, salidas e internos.",
    "Lo que puede consultar no está acotado por su dependencia, sino por su "
    "nivel de acceso a la información.",
)


def _nivel_legible(max_level: int) -> str:
    return _NIVELES.get(max_level, f"nivel {max_level}")


async def _leer(
    gateway_client: httpx.AsyncClient, ruta: str, *, authorization: str, tenant_slug: str
) -> dict[str, Any] | None:
    """Lectura best-effort de un complemento del marco.

    Si falla, el marco se entrega sin ese dato en vez de no entregarse: saber qué
    puede hacer es útil aunque no se sepa la dependencia activa. Lo que NO es
    admisible es inventarlo.
    """
    try:
        resp = await gateway_client.get(
            ruta, headers={"Authorization": authorization, "X-Tenant-Slug": tenant_slug}
        )
    except httpx.RequestError:
        return None
    if resp.status_code != 200:
        return None
    try:
        datos = resp.json()
    except ValueError:
        return None
    return datos if isinstance(datos, dict) else None


def _entrada(tool: Tool) -> dict[str, str]:
    return {"operacion": tool.operacion or tool.name, "herramienta": tool.name}


def _motivo(tool: Tool, identidad: Identity) -> str:
    """Por qué no puede: falta el permiso, o lo tiene pero sin nivel suficiente.

    La distinción importa. «No tiene el permiso» se resuelve pidiéndoselo a quien
    administra; «lo tiene, pero solo para consultar» es otra conversación, y
    confundirlas manda a la persona a la puerta equivocada.
    """
    if tool.permission and tool.permission not in identidad.permissions:
        return f"requiere el permiso {tool.permission}, que su cuenta no tiene"
    nivel = identidad.levels.get(tool.permission or "", 0)
    return (
        f"tiene el permiso {tool.permission}, pero solo con nivel de consulta; "
        "esta acción exige nivel de escritura"
    ) if nivel else f"requiere el permiso {tool.permission}"


async def build_scope(
    gateway_client: httpx.AsyncClient,
    identidad: Identity,
    *,
    authorization: str,
    tenant_slug: str,
) -> dict[str, Any]:
    """Compone el marco de alcance de esta persona, aquí y ahora."""
    clearance = await _leer(
        gateway_client, "/api/v1/auth/clearance", authorization=authorization, tenant_slug=tenant_slug
    )
    contexto = await _leer(
        gateway_client, "/api/v1/auth/context", authorization=authorization, tenant_slug=tenant_slug
    )

    puede_hacer: list[dict[str, str]] = []
    puede_consultar: list[dict[str, str]] = []
    no_puede: list[dict[str, str]] = []

    for tool in catalog.CATALOG:
        if catalog.puede_usar(
            tool, identidad.permissions, is_root=identidad.is_root, levels=identidad.levels
        ):
            (puede_hacer if tool.write else puede_consultar).append(_entrada(tool))
            continue
        fuera = _entrada(tool) | {"motivo": _motivo(tool, identidad)}
        if tool.fallback:
            fuera["via_alternativa"] = tool.fallback
        no_puede.append(fuera)

    marco: dict[str, Any] = {
        "usuario": {
            "entidad": identidad.tenant_slug,
            "es_root": identidad.is_root,
        },
        "puede_hacer": puede_hacer,
        "puede_consultar": puede_consultar,
        "no_puede": no_puede,
        "limites_declarados": list(LIMITES_DECLARADOS),
    }

    if clearance is not None:
        nivel = int(clearance.get("max_level") or 1)
        marco["acceso"] = {
            "nivel_maximo": nivel,
            "etiqueta": _nivel_legible(nivel),
            "explicacion": (
                f"Puede ver información hasta el nivel {_nivel_legible(nivel)}. "
                "Lo que esté por encima no aparece en sus búsquedas ni en sus "
                "consultas, y el sistema no revela que exista."
            ),
        }
    if contexto is not None:
        marco["usuario"]["dependencia_activa"] = contexto.get("active_depe_id")
        marco["usuario"]["dependencias_disponibles"] = len(contexto.get("available_depts") or [])

    return marco


def marco_a_texto(marco: dict[str, Any]) -> str:
    """El marco en prosa, para inyectarlo en el prompt del asistente.

    Va como texto y no como JSON a propósito: es una instrucción de trabajo para
    el modelo, no un dato que deba reproducir. Y se redacta aquí, no en el
    catálogo ni en el modelo, para que lo que se le dice al asistente y lo que se
    le enseña a la persona salgan de la misma fuente.
    """
    lineas: list[str] = ["ALCANCE DE ESTE USUARIO (calculado ahora, no negociable):"]

    if marco.get("puede_hacer"):
        lineas.append("Puede EJECUTAR estas acciones (siempre con confirmación previa):")
        lineas += [f"- {e['operacion']}" for e in marco["puede_hacer"]]
    else:
        lineas.append("No puede ejecutar ninguna acción de escritura.")

    if marco.get("puede_consultar"):
        lineas.append("Puede CONSULTAR:")
        lineas += [f"- {e['operacion']}" for e in marco["puede_consultar"]]

    if marco.get("no_puede"):
        lineas.append(
            "NO puede lo siguiente. Si lo pide, dilo de inmediato con su motivo y su vía "
            "alternativa, y NO intentes la acción ni inicies trabajo hacia ella:"
        )
        for e in marco["no_puede"]:
            via = f" Vía alternativa: {e['via_alternativa']}" if e.get("via_alternativa") else ""
            lineas.append(f"- {e['operacion']} — {e['motivo']}.{via}")

    acceso = marco.get("acceso")
    if acceso:
        lineas.append(acceso["explicacion"])

    if marco.get("limites_declarados"):
        lineas.append(
            "Precisiones que NO debes afirmar porque el sistema no las distingue:"
        )
        lineas += [f"- {t}" for t in marco["limites_declarados"]]

    return "\n".join(lineas)
