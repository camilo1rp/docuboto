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


# Actos que este servidor NO ofrece **por decisión**, no por falta de permiso.
#
# Van declarados y no simplemente ausentes por la misma razón que existe el resto
# del marco: un catálogo recortado en silencio hace que el límite se descubra
# pidiendo algo que nunca va a ocurrir. Aquí se dice qué no se puede hacer por
# este canal, por qué, y dónde sí se puede.
#
# Los criterios para entrar en esta lista son tres, y conviene tenerlos
# separados porque protegen cosas distintas:
#
# 1. **Actos cuya garantía depende de que haya una persona presente**, y que
#    este canal no puede aportar. La tarjeta de confirmación encarece el
#    descuido; no convierte a un modelo en una persona. Cuando lo que está en
#    juego es la destrucción de documentos de archivo, esa diferencia deja de
#    ser teórica.
# 2. **Actos que mueven la propia frontera de autorización.** Todo este marco
#    consiste en trabajar DENTRO de los límites de quien pregunta; un acto que
#    ensancha esos límites —conceder un permiso, subir un nivel de acceso,
#    meter a alguien en un grupo— los deja de tener. Una frontera alcanzable
#    desde dentro no es una frontera.
# 3. **Actos que sacan un secreto de un solo uso a un canal que no lo
#    custodia.** El secreto TOTP y los códigos de recuperación se revelan
#    exactamente una vez, por diseño: entregarlos aquí los dejaría escritos en
#    el historial de una conversación.
#
# Un cuarto motivo, más callado, gobierna el calendario y el vocabulario: no
# son actos peligrosos, son la REGLA con la que se mide todo lo demás. Un
# festivo añadido hoy mueve vencimientos que ya están corriendo, y un catálogo
# al que se le añaden entradas desde una conversación deja de ser cerrado, que
# era lo único que lo hacía servir para algo.
NO_OFRECIDAS: tuple[dict[str, str], ...] = (
    {
        "operacion": "Ejecutar el borrado de un lote de eliminación",
        "motivo": (
            "Es el único acto del sistema que destruye documentos de archivo, y no se "
            "deshace. Se ofrecen las cuatro fases previas —proponer, publicar, avalar y "
            "el acta—, pero la ejecución exige una persona presente y responsable, y "
            "este canal no puede garantizarla: quien teclea la confirmación puede ser el "
            "modelo"
        ),
        "via_alternativa": (
            "Deje el lote avalado y con su acta firmada; la ejecución se hace desde la "
            "interfaz del sistema, con sesión propia"
        ),
    },
    {
        "operacion": "Sellar un artefacto en WORM (Object Lock)",
        "motivo": (
            "El sellado exige el identificador interno del artefacto firmado, que solo "
            "conoce el servicio de archivo en el contexto del expediente: quien lo pidiera "
            "por aquí tendría que inventárselo, y sellar el fichero equivocado bajo un "
            "candado de diez años no se deshace —la retención no se acorta—. Una tarjeta "
            "de confirmación no protege de esto: enseñaría un identificador que la persona "
            "no tiene forma de contrastar. Por lo mismo tampoco lo ofrece la interfaz"
        ),
        "via_alternativa": (
            "El sellado ocurre solo al cerrar el expediente. Puede consultar qué quedó "
            "pendiente de firma o de renovación, y reintentar la firma del índice"
        ),
    },
    {
        "operacion": "Empaquetar manualmente el AIP de preservación",
        "motivo": (
            "Es un acto de reconciliación del sistema, no una decisión: exige la lista de "
            "documentos que solo el servicio de archivo compone, y se dispara al cerrar el "
            "expediente"
        ),
        "via_alternativa": "Consulte el AIP ya empaquetado del expediente",
    },
    {
        "operacion": "Armar la estructura de un plan PINAR (objetivos, proyectos, priorización)",
        "motivo": (
            "Formular un Plan Institucional de Archivos es un ejercicio de planeación con "
            "el Comité delante —qué aspectos críticos pesan más, qué se hace primero—, no "
            "una secuencia de altas. Se ofrece consultarlo y registrar avance"
        ),
        "via_alternativa": (
            "Consulte el tablero, el mapa de ruta y los proyectos, y anote el avance de "
            "los que ya existen; la formulación se hace en «Administración → PINAR»"
        ),
    },
    {
        "operacion": "Firmar personalmente el acta de una transferencia",
        "motivo": (
            "La firma personal es un acto de identidad con segundo factor (TOTP): "
            "pedirlo por un chat trasladaría el código de un solo uso a un canal que no "
            "lo custodia"
        ),
        "via_alternativa": "Firme el acta desde la interfaz, en «Transferencias»",
    },
    {
        "operacion": (
            "Conceder o retirar permisos, cambiar el nivel de acceso de un grupo, o "
            "crear personas y grupos"
        ),
        "motivo": (
            "Es el acto que decide qué puede hacer todo el mundo, este asistente "
            "incluido: si se pudiera ejecutar desde aquí, el marco de alcance dejaría "
            "de ser un límite y pasaría a ser una sugerencia. La habilitación de una "
            "persona necesita además quedar atribuida a quien la decidió, con su sesión "
            "y su rastro"
        ),
        "via_alternativa": (
            "Puede consultarlo todo —grupos, miembros, permisos con su nivel, unidades "
            "de reparto y nivel de acceso— para preparar el cambio; concederlo se hace "
            "en «Administración → Usuarios y grupos»"
        ),
    },
    {
        "operacion": (
            "Dar de alta o revocar credenciales personales: segundo factor (TOTP), "
            "códigos de recuperación y llave de firma"
        ),
        "motivo": (
            "El secreto y los códigos de recuperación se revelan EXACTAMENTE UNA VEZ, "
            "por diseño; entregarlos por este canal los dejaría escritos en el "
            "historial de una conversación. Y revocar el segundo factor de otra persona "
            "es el paso previo a suplantarla"
        ),
        "via_alternativa": (
            "Puede consultar el ESTADO de sus credenciales —si el segundo factor está "
            "activo, si la llave de firma vigente— para saber antes de intentar una "
            "firma si va a poder completarla; darlas de alta se hace en «Mi perfil»"
        ),
    },
    {
        "operacion": (
            "Cambiar el calendario de festivos o los parámetros de configuración de la "
            "entidad"
        ),
        "motivo": (
            "Son la regla con la que se cuentan los términos de ley. Un festivo que se "
            "añade hoy corre el vencimiento de todos los plazos que ya están andando, "
            "sin avisar a nadie y sin que aparezca en la traza de ningún expediente"
        ),
        "via_alternativa": (
            "Puede consultar los festivos y calcular cualquier vencimiento en días "
            "hábiles; cambiarlos se hace en «Administración → Parámetros»"
        ),
    },
    {
        "operacion": (
            "Añadir o modificar valores de los catálogos, o cambiar la estructura de "
            "dependencias"
        ),
        "motivo": (
            "El valor de un catálogo cerrado es precisamente que sea cerrado: en cuanto "
            "se le pueden añadir entradas sobre la marcha aparecen «Bogotá», «BOGOTA» y "
            "«Bogotá D.C.» como tres cosas distintas, y ninguna búsqueda posterior las "
            "vuelve a reunir. La estructura de dependencias, además, decide a quién le "
            "llegan los documentos"
        ),
        "via_alternativa": (
            "Puede consultar los catálogos y el árbol de dependencias para elegir el "
            "valor correcto; si falta uno, se pide al administrador"
        ),
    },
    {
        "operacion": (
            "Configurar los buzones de correo: registrar la aplicación OAuth de la "
            "entidad, dar de alta una cuenta o autorizarla ante el proveedor"
        ),
        "motivo": (
            "Todo eso es mover credenciales —el secreto de la aplicación ante Google o "
            "Microsoft, la contraseña del buzón, el código de autorización—, y ninguna "
            "puede viajar por un chat. Trabajar el correo recibido sí se ofrece entero: "
            "lo que no se ofrece es enchufar el buzón"
        ),
        "via_alternativa": (
            "Los buzones se configuran en «Administración → Correo»; una vez "
            "conectados, puede sincronizar, reservar, radicar y descartar desde aquí"
        ),
    },
    {
        "operacion": "Trabajar un buzón PERSONAL",
        "motivo": (
            "La contraseña de un buzón personal no se guarda, a propósito: viaja en "
            "cada petición y muere con ella. Trabajarlo desde aquí exigiría teclearla "
            "en la conversación"
        ),
        "via_alternativa": (
            "Los buzones institucionales sí se trabajan por este medio; el personal, "
            "desde la pantalla de correo"
        ),
    },
    {
        "operacion": "Dar de alta o retirar un canal de salida (webhook)",
        "motivo": (
            "Un webhook manda los eventos documentales de la entidad a una URL de "
            "fuera: crearlo es abrir un canal de salida permanente hacia un tercero, y "
            "eso no se decide en una conversación. Consultar cuáles hay, sí"
        ),
        "via_alternativa": (
            "Puede ver los canales configurados; darlos de alta o retirarlos se hace en "
            "«Administración → Notificaciones»"
        ),
    },
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
        # Clave APARTE en el JSON y JUNTAS en la prosa, y no es una
        # inconsistencia: las entradas de `no_puede` llevan el nombre de su tool
        # —hay consumidores que indexan por él— y un acto que no se ofrece no
        # tiene tool que nombrar. Meterlo ahí con el nombre vacío rompería ese
        # contrato. Lo que sí importa es que quien LEE el marco encuentre las
        # dos cosas en el mismo sitio, porque para quien pregunta «¿puedo pedir
        # esto?» la distinción entre «te falta permiso» y «este canal no lo
        # hace» es irrelevante — y eso se resuelve en `marco_a_texto`.
        "no_ofrecidas": [dict(x) for x in NO_OFRECIDAS],
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

    negados = [*marco.get("no_puede", []), *marco.get("no_ofrecidas", [])]
    if negados:
        lineas.append(
            "NO puede lo siguiente. Si lo pide, dilo de inmediato con su motivo y su vía "
            "alternativa, y NO intentes la acción ni inicies trabajo hacia ella:"
        )
        for e in negados:
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
