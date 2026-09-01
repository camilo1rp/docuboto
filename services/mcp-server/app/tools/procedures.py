# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Procedimientos: el trámite entero, no la llamada suelta (E18, ADR-019).

El catálogo de tools traduce **una** tool a **un** endpoint. Eso alcanza para
consultar, y se queda corto para lo que de verdad hace un funcionario: radicar
una petición no es un acto, son cuatro —radicar, repartir, expedientar, avisar—
y responderla son otros cuatro. Con tools sueltas, quien las encadena es el
modelo: decide el orden, se inventa los enlaces entre un paso y el siguiente, y
si algo falla a mitad nadie sabe en qué estado quedó el trámite.

Un procedimiento declara esa secuencia **en el servidor**, con nombre y con
orden fijo. Sigue siendo declarativo —una entrada en una tabla, no código nuevo
por procedimiento— y sigue apoyándose en las tools existentes: un procedimiento
no habla con la base ni con los servicios, encadena tools.

── Tres propiedades que lo hacen seguro, y por qué ─────────────────────────

**1. Cada paso viaja con el token de la PERSONA.** No hay credencial de
procedimiento ni escalada: si el usuario no puede asignar, el paso de asignar
devuelve 403 igual que si lo hubiera llamado a mano. Un procedimiento no puede
hacer nada que su usuario no pueda hacer — lo único que aporta es el orden y el
enlace entre pasos.

**2. No hay transacción distribuida, y NO se finge.** Los pasos son llamadas
HTTP a servicios distintos; si el tercero falla, los dos primeros ya ocurrieron.
Y no se pueden deshacer: **radicar consume un número inmutable** que no se
devuelve al mostrador. Por eso el resultado dice paso por paso qué se hizo y
dónde se detuvo, en vez de un «falló» que dejaría a quien pregunta sin saber si
tiene un radicado a medias. Fingir atomicidad aquí sería la mentira más cara del
sistema.

**3. El orden es del dominio, no una comodidad.** Se radica antes de repartir
porque sin número no hay nada que repartir; se aprueba el borrador antes de
radicarlo porque un borrador sin aprobar no es un documento oficial. El orden va
declarado y fijo justamente para que el modelo no lo elija.

── El pequeño lenguaje de enlace entre pasos ───────────────────────────────

Cada paso mapea los argumentos de su tool a:

* ``"@nombre"`` — un argumento del procedimiento;
* ``"#etiqueta.campo"`` — un campo de la respuesta de un paso ANTERIOR (por su
  etiqueta), que es lo que ata el número de radicado recién creado al paso que
  lo reparte;
* cualquier otro valor — literal.

Un ``"#paso.campo"`` que no exista **aborta**: es la forma que tendría un
procedimiento de mandar `None` donde va un identificador y crear un vínculo
huérfano sin que nada chille.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.tools.catalog import Param, Tool, get_tool


class ProcedureError(Exception):
    """Un procedimiento no se pudo preparar (declaración o enlace inválidos)."""


@dataclass(frozen=True)
class Paso:
    """Un eslabón: una tool del catálogo con sus argumentos ya resueltos.

    `opcional` NO significa «da igual si falla». Significa que su fallo no
    invalida lo ya hecho y el procedimiento sigue, informándolo: registrar el
    envío postal de una respuesta ya radicada es exactamente eso — si el envío
    falla, la respuesta sigue siendo un documento oficial válido y volver a
    radicarla sería el peor remedio posible.
    """

    etiqueta: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    opcional: bool = False
    # Se ejecuta solo si el procedimiento recibió este argumento. Sirve para los
    # pasos que dependen de un dato que la persona puede no tener a mano.
    si_hay: str | None = None


@dataclass(frozen=True)
class Procedimiento:
    name: str
    description: str
    params: tuple[Param, ...]
    pasos: tuple[Paso, ...]
    # Un procedimiento con cualquier paso de escritura ES una escritura, y su
    # confirmación es UNA sola para el acto completo: pedir cuatro confirmaciones
    # seguidas enseña a confirmar sin leer, que es peor que no preguntar.
    write: bool = True
    destructive: bool | None = None
    permission: str | None = None
    min_crud: int = 1

    # `exactly_one_of` y `operacion` existen para que un procedimiento sea
    # ESTRUCTURALMENTE una tool a ojos de `coerce_args`, `card_fields` y
    # `puede_usar`: así la validación de argumentos, la tarjeta de confirmación
    # y el gate de permisos son EXACTAMENTE los mismos que para una tool suelta.
    # Reimplementarlos aquí habría creado una segunda validación que se
    # desalinea en cuanto alguien toque una de las dos.
    exactly_one_of: tuple[tuple[str, ...], ...] = ()
    operacion: str = ""
    # Un procedimiento también puede exigir que se teclee el valor de uno de sus
    # argumentos para confirmarse (ver `Tool.confirmar_con`). Hoy ninguno lo
    # usa: los que existen no habilitan una destrucción. Se declara igual porque
    # el gate es EL MISMO —`write_gate.frase_requerida`— y dejar el campo fuera
    # obligaría a un camino aparte el día que haga falta, que es como se separan
    # dos controles que deberían ser uno.
    confirmar_con: str | None = None

    def to_schema(self) -> dict:
        """La misma forma que `Tool.to_schema`, más los pasos.

        Los pasos VIAJAN al cliente a propósito: quien va a confirmar un
        procedimiento tiene derecho a saber qué cadena de actos está aprobando.
        Un «tramitar petición» opaco sería pedir una firma en blanco.
        """
        return {
            "name": self.name,
            "description": self.description,
            "method": "PROCEDIMIENTO",
            "write": self.write,
            "epic": "E18",
            "pasos": [
                {"paso": paso.etiqueta, "tool": paso.tool, "opcional": paso.opcional}
                for paso in self.pasos
            ],
        }

    @property
    def params_by_name(self) -> dict[str, Param]:
        return {p.name: p for p in self.params}


def resolver_args(paso: Paso, entrada: dict[str, Any], salidas: dict[str, Any]) -> dict[str, Any]:
    """Traduce el mapeo declarado a los argumentos concretos de la tool.

    Los `@argumento` ausentes se OMITEN (el argumento simplemente no viaja), que
    es lo correcto para los opcionales. Los `#paso.campo` ausentes ABORTAN: si
    el paso anterior no devolvió el identificador, seguir significaría enlazar
    contra `None` y crear un vínculo huérfano — el fallo silencioso que este
    sistema ya conoce.
    """
    resueltos: dict[str, Any] = {}
    for destino, origen in paso.args.items():
        if isinstance(origen, str) and origen.startswith("@"):
            clave = origen[1:]
            if clave in entrada and entrada[clave] is not None:
                resueltos[destino] = entrada[clave]
            continue
        if isinstance(origen, str) and origen.startswith("#"):
            etiqueta, _, campo = origen[1:].partition(".")
            cuerpo = salidas.get(etiqueta)
            if not isinstance(cuerpo, dict) or campo not in cuerpo:
                raise ProcedureError(
                    f"el paso «{paso.etiqueta}» necesita «{campo}» del paso «{etiqueta}» "
                    "y ese paso no lo devolvió"
                )
            resueltos[destino] = cuerpo[campo]
            continue
        resueltos[destino] = origen
    return resueltos


def tool_de(paso: Paso) -> Tool:
    herramienta = get_tool(paso.tool)
    if herramienta is None:
        raise ProcedureError(f"el paso «{paso.etiqueta}» nombra una tool inexistente: {paso.tool}")
    return herramienta


# ---------------------------------------------------------------------------
# El catálogo de procedimientos
#
# Cada uno es un trámite reconocible por un funcionario, no una agrupación
# técnica: si alguien de ventanilla no le pondría ese nombre a lo que hace,
# probablemente no es un procedimiento sino dos tools seguidas.
# ---------------------------------------------------------------------------

PROCEDIMIENTOS: tuple[Procedimiento, ...] = (
    Procedimiento(
        name="tramitar_peticion_ciudadana",
        description=(
            "Radica una comunicación oficial de ENTRADA y la deja en la bandeja de "
            "la dependencia que debe atenderla; opcionalmente la vincula a un "
            "expediente existente. Es el trámite de ventanilla completo: sin el "
            "reparto, el radicado existe y no llega a nadie."
        ),
        permission="PERM_RADI",
        min_crud=3,
        write=True,
        # Radicar consume un número inmutable que no se devuelve: el acto no se
        # puede deshacer, y quien confirma tiene que saberlo antes, no después.
        destructive=True,
        params=(
            Param(
                name="subject",
                required=True,
                description="Asunto de la comunicación, tal como lo expresa el ciudadano.",
                label="Asunto",
            ),
            Param(
                name="sender_name",
                required=True,
                description="Nombre de quien remite.",
                label="Remitente",
            ),
            Param(name="sender_entity", description="Entidad del remitente, si aplica."),
            Param(name="doc_class", description="Código del tipo documental (catálogo de la TRD)."),
            # Los nombres son los que declara `radicar_documento`, no los del
            # endpoint: el procedimiento encadena TOOLS, y validar contra otra
            # cosa produciría un 400 en el primer paso — con el agravante de que
            # el modelo habría anunciado a la persona un argumento inexistente.
            Param(
                name="origin_dept_code",
                type="integer",
                description="Código de la dependencia que recibe la comunicación.",
            ),
            Param(
                name="dest_dept_code",
                type="integer",
                required=True,
                description="Código de la dependencia que debe atenderla.",
                label="Dependencia destino",
            ),
            Param(
                name="to_dept",
                required=True,
                description="Nombre de la dependencia a la que se reparte.",
                label="Reparto a",
            ),
            Param(name="pages", type="integer", description="Número de folios."),
            Param(name="observations", description="Observaciones de la radicación."),
            Param(
                name="response_days",
                type="integer",
                description="Días hábiles para responder (plazo legal del trámite).",
            ),
            Param(
                name="assigned_to",
                format="uuid",
                description="Persona a la que se asigna dentro de la dependencia destino.",
            ),
            Param(
                name="expediente_id",
                format="uuid",
                description=(
                    "Expediente al que pertenece el asunto. Si se omite, el radicado "
                    "queda sin expedientar y habrá que hacerlo después."
                ),
            ),
        ),
        pasos=(
            Paso(
                etiqueta="radicar",
                tool="radicar_documento",
                args={
                    "doc_type": "E",
                    "subject": "@subject",
                    "sender_name": "@sender_name",
                    "sender_entity": "@sender_entity",
                    "doc_class": "@doc_class",
                    "origin_dept_code": "@origin_dept_code",
                    "dest_dept_code": "@dest_dept_code",
                    "pages": "@pages",
                    "observations": "@observations",
                    "response_days": "@response_days",
                },
            ),
            Paso(
                etiqueta="repartir",
                tool="asignar_radicado",
                args={
                    "action": "assign",
                    "radicado_id": "#radicar.id",
                    "tracking_number": "#radicar.tracking_number",
                    "to_dept": "@to_dept",
                    "assigned_to": "@assigned_to",
                    "doc_type": "E",
                },
            ),
            Paso(
                etiqueta="expedientar",
                tool="vincular_radicado",
                si_hay="expediente_id",
                args={
                    "expediente_id": "@expediente_id",
                    "radicado_id": "#radicar.id",
                    "tracking_number": "#radicar.tracking_number",
                },
            ),
        ),
    ),
    Procedimiento(
        name="proyectar_y_radicar_respuesta",
        description=(
            "Lleva una respuesta desde el borrador hasta el número de radicado: "
            "redacta, aprueba y radica la Salida. Un borrador sin aprobar no es un "
            "documento oficial, y este es el circuito formal con revisión.\n\n"
            "**No la vincula al radicado que la originó**: el sistema crea el vínculo "
            "de antecedente al CREAR la Salida, no después, y para eso está "
            "`crear_respuesta_vinculada` —que la radica ya vinculada, sin pasar por "
            "borrador ni aprobación—. Elige según lo que necesites: circuito con "
            "revisión, o vínculo de antecedente."
        ),
        permission="PERM_RESPUESTA_VINCULADA",
        min_crud=3,
        write=True,
        destructive=True,
        params=(
            Param(
                name="asunto",
                required=True,
                description="Asunto de la respuesta.",
                label="Asunto",
            ),
            Param(
                name="cuerpo",
                required=True,
                description="Texto de la respuesta.",
                label="Cuerpo",
            ),
            Param(name="dest_dept", description="Dependencia que produce la respuesta."),
        ),
        pasos=(
            Paso(
                etiqueta="redactar",
                tool="crear_borrador",
                args={"subject": "@asunto", "cuerpo": "@cuerpo", "dest_dept": "@dest_dept"},
            ),
            Paso(etiqueta="aprobar", tool="aprobar_borrador", args={"borrador_id": "#redactar.id"}),
            Paso(etiqueta="radicar", tool="radicar_borrador", args={"borrador_id": "#redactar.id"}),
        ),
    ),
    Procedimiento(
        name="conformar_expediente",
        description=(
            "Abre un expediente sobre una serie de la TRD y le vincula los radicados "
            "indicados, en el orden dado. No lo cierra: cerrar congela la foliación, "
            "dispara el índice electrónico y su firma, y es un acto aparte que debe "
            "decidir una persona cuando el asunto termina."
        ),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        destructive=False,
        params=(
            Param(name="name", required=True, description="Nombre del expediente.", label="Nombre"),
            Param(name="description", description="Descripción del asunto."),
            Param(
                name="trd_serie_id",
                format="uuid",
                description="Serie de la TRD que fija su retención y disposición.",
            ),
            Param(
                name="radicado_id",
                required=True,
                format="uuid",
                description="Radicado que se vincula al abrirlo.",
                label="Radicado",
            ),
            Param(
                name="tracking_number",
                required=True,
                description="Número del radicado que se vincula.",
            ),
            Param(name="folios", type="integer", description="Folios que aporta ese radicado."),
        ),
        pasos=(
            Paso(
                etiqueta="abrir",
                tool="crear_expediente",
                args={
                    "name": "@name",
                    "description": "@description",
                    "trd_serie_id": "@trd_serie_id",
                },
            ),
            Paso(
                etiqueta="vincular",
                tool="vincular_radicado",
                args={
                    "expediente_id": "#abrir.id",
                    "radicado_id": "@radicado_id",
                    "tracking_number": "@tracking_number",
                    "folios": "@folios",
                },
            ),
        ),
    ),
    Procedimiento(
        name="ubicar_expediente",
        description=(
            "Deja un expediente COLOCADO en el depósito: crea la unidad de conservación "
            "en la ubicación indicada y mete el expediente dentro, con su rango de "
            "folios. Sin el segundo paso la caja existe y el expediente sigue sin "
            "aparecer cuando alguien pregunta dónde está."
        ),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=True,
        # Crear una caja y meter un expediente en ella se corrige moviéndolo: no
        # consume nada irrepetible, a diferencia de radicar.
        destructive=False,
        params=(
            Param(
                name="tipo",
                required=True,
                enum=("caja", "carpeta", "legajo", "libro", "tomo", "az", "folder"),
                description="Tipo de unidad de conservación.",
                label="Tipo de unidad",
            ),
            Param(name="codigo", required=True, max_length=50, description="Código de la unidad.", label="Código"),
            Param(
                name="ubicacion_id",
                required=True,
                format="uuid",
                description="Ubicación física donde se coloca la unidad.",
                label="Ubicación",
            ),
            Param(
                name="expediente_id",
                required=True,
                format="uuid",
                description="Expediente que se guarda en ella.",
                label="Expediente",
            ),
            Param(name="soporte", max_length=50, description="Soporte del material.", label="Soporte"),
            Param(name="num_folios", type="integer", minimum=0, description="Folios de la unidad.", label="Folios"),
            Param(name="folio_inicio", type="integer", minimum=0, description="Primer folio del expediente en la unidad.", label="Folio inicial"),
            Param(name="folio_fin", type="integer", minimum=0, description="Último folio del expediente en la unidad.", label="Folio final"),
        ),
        pasos=(
            Paso(
                etiqueta="crear_unidad",
                tool="crear_unidad",
                args={
                    "tipo": "@tipo",
                    "codigo": "@codigo",
                    "ubicacion_id": "@ubicacion_id",
                    "soporte": "@soporte",
                    "num_folios": "@num_folios",
                },
            ),
            Paso(
                etiqueta="guardar",
                tool="ubicar_expediente_en_unidad",
                args={
                    "unidad_id": "#crear_unidad.id",
                    "expediente_id": "@expediente_id",
                    "folio_inicio": "@folio_inicio",
                    "folio_fin": "@folio_fin",
                },
            ),
        ),
    ),
    Procedimiento(
        name="preparar_transferencia",
        description=(
            "Prepara y REMITE la transferencia de un expediente cerrado al archivo "
            "receptor: la crea y la envía. NO la recibe — recibir es del receptor, y "
            "que una sola llamada hiciera las dos puntas convertiría una entrega entre "
            "dos responsables en un trámite de uno solo."
        ),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        # Al enviarla, la custodia del expediente cambia de manos y queda en
        # tránsito: no se «des-envía».
        destructive=True,
        params=(
            Param(
                name="tipo",
                required=True,
                enum=("primaria", "secundaria"),
                description="Primaria: gestión→central. Secundaria: central→histórico.",
                label="Tipo",
            ),
            Param(
                name="expediente_id",
                required=True,
                format="uuid",
                description="Expediente que se transfiere. Debe estar CERRADO.",
                label="Expediente",
            ),
            Param(name="ubicacion_origen_id", format="uuid", description="Ubicación de la que sale.", label="Origen"),
            Param(name="ubicacion_destino_id", format="uuid", description="Ubicación a la que va.", label="Destino"),
            Param(name="observacion", max_length=2000, description="Observaciones de la entrega.", label="Observación"),
        ),
        pasos=(
            Paso(
                etiqueta="crear",
                tool="crear_transferencia",
                args={
                    "tipo": "@tipo",
                    "expediente_id": "@expediente_id",
                    "ubicacion_origen_id": "@ubicacion_origen_id",
                    "ubicacion_destino_id": "@ubicacion_destino_id",
                    "observacion": "@observacion",
                },
            ),
            Paso(etiqueta="enviar", tool="enviar_transferencia", args={"transferencia_id": "#crear.id"}),
        ),
    ),
    Procedimiento(
        name="estado_del_expediente",
        description=(
            "Todo sobre un expediente en una respuesta: sus datos, los radicados que "
            "contiene, su foliación, su índice electrónico y dónde está físicamente. "
            "Solo lectura."
        ),
        write=False,
        destructive=False,
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        params=(
            Param(
                name="expediente_id",
                required=True,
                format="uuid",
                description="Expediente a consultar.",
                label="Expediente",
            ),
        ),
        pasos=(
            # `consultar_expediente` YA devuelve los radicados vinculados: no hay
            # `GET /expedientes/{id}/radicados` y añadir un paso para pedirlos
            # habría sido inventar un endpoint — el mismo error que ya cazó el
            # test al declarar los argumentos desde el endpoint y no desde la tool.
            Paso(etiqueta="expediente", tool="consultar_expediente", args={"expediente_id": "@expediente_id"}),
            # Opcionales: un expediente recién abierto no tiene foliación, ni
            # índice, ni caja. Que falten no es un fallo de la consulta —
            # devolver error obligaría a preguntar pieza por pieza.
            Paso(etiqueta="foliado", tool="consultar_foliado", args={"expediente_id": "@expediente_id"}, opcional=True),
            Paso(etiqueta="indice", tool="indice_expediente", args={"expediente_id": "@expediente_id"}, opcional=True),
            Paso(etiqueta="ubicacion", tool="ubicacion_expediente", args={"expediente_id": "@expediente_id"}, opcional=True),
        ),
    ),
    Procedimiento(
        name="estado_del_tramite",
        description=(
            "Todo lo que hay que saber de un radicado en una sola respuesta: sus "
            "datos, en qué paso del flujo está, su hoja de ruta, sus anexos y las "
            "respuestas que ha generado. Solo lectura."
        ),
        write=False,
        destructive=False,
        params=(
            Param(
                name="document_id",
                required=True,
                format="uuid",
                description="Radicado a consultar.",
                label="Radicado",
            ),
        ),
        pasos=(
            Paso(etiqueta="documento", tool="consultar_radicado", args={"document_id": "@document_id"}),
            # Los tres siguientes son OPCIONALES a propósito: un radicado recién
            # creado no tiene paso de flujo ni respuestas, y que falte no es un
            # fallo de la consulta — devolver un error por eso obligaría a
            # preguntar de nuevo pieza por pieza, que es justo lo que este
            # procedimiento existe para evitar.
            Paso(
                etiqueta="paso_actual",
                tool="paso_actual",
                args={"radicado_id": "@document_id"},
                opcional=True,
            ),
            Paso(
                etiqueta="hoja_de_ruta",
                tool="hoja_ruta_radicado",
                args={"radicado_id": "@document_id"},
                opcional=True,
            ),
            Paso(
                etiqueta="anexos",
                tool="listar_anexos",
                args={"document_id": "@document_id"},
                opcional=True,
            ),
            Paso(
                etiqueta="respuestas",
                tool="listar_respuestas",
                args={"document_id": "@document_id"},
                opcional=True,
            ),
        ),
    ),
    Procedimiento(
        name="atender_correo_del_buzon",
        description=(
            "Toma un correo de la bandeja y lo radica como documento de ENTRADA, en el "
            "orden que exige el sistema: primero la reserva —sin ella el backend "
            "rechaza radicar, y es lo que impide que dos funcionarios radiquen el mismo "
            "correo dos veces— y luego la radicación. Consume un número de radicado, "
            "que es inmutable; y sobre una Entrada la anulación no existe, así que "
            "radicar el correo equivocado no se deshace."
        ),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=True,
        operacion="Atender un correo del buzón: reservarlo y radicarlo",
        params=(
            Param(
                name="cuenta_id",
                required=True,
                format="uuid",
                description="UUID del buzón, de 'listar_buzones_correo'.",
                label="Buzón",
            ),
            Param(
                name="mensaje_id",
                required=True,
                format="uuid",
                description="UUID del correo, de 'listar_correos_pendientes'.",
                label="Correo",
            ),
            Param(
                name="subject",
                description="Asunto del radicado. Si se omite, el del correo.",
                label="Asunto",
            ),
            Param(
                name="sender_name",
                description="Remitente. Si se omite, el que trae el correo.",
                label="Remitente",
            ),
            Param(
                name="dest_dept_code",
                type="integer",
                required=True,
                description=(
                    "CÓDIGO de la dependencia competente, de 'listar_dependencias'. Se "
                    "exige aquí aunque el endpoint lo admita vacío: un radicado sin "
                    "dependencia no llega a nadie, y el trámite completo consiste "
                    "precisamente en que llegue."
                ),
                label="Dependencia competente",
            ),
            Param(
                name="doc_class",
                description="Código del tipo documental (catálogo de la TRD).",
                label="Tipo documental",
            ),
        ),
        # Dos pasos y NO tres: liberar en caso de fallo no se encadena aquí. Si
        # la radicación falla, la reserva queda puesta y caduca sola; soltarla
        # automáticamente sería decidir por la persona que ya no quiere ese
        # correo, cuando lo normal tras un fallo es volver a intentarlo.
        pasos=(
            Paso(
                etiqueta="reservar",
                tool="reservar_correo",
                args={"cuenta_id": "@cuenta_id", "mensaje_id": "@mensaje_id"},
            ),
            Paso(
                etiqueta="radicar",
                tool="radicar_correo",
                args={
                    "cuenta_id": "@cuenta_id",
                    "mensaje_id": "@mensaje_id",
                    "subject": "@subject",
                    "sender_name": "@sender_name",
                    "dest_dept_code": "@dest_dept_code",
                    "doc_class": "@doc_class",
                },
            ),
        ),
    ),
)

_POR_NOMBRE = {p.name: p for p in PROCEDIMIENTOS}


def get_procedimiento(name: str) -> Procedimiento | None:
    return _POR_NOMBRE.get(name)


def procedimientos_visibles(
    permissions: set[str], is_root: bool = False, levels: dict[str, int] | None = None
) -> list[Procedimiento]:
    """Los procedimientos que este usuario alcanza.

    Usa `puede_usar` del catálogo, el MISMO gate que filtra las tools: un
    segundo criterio aquí acabaría divergiendo, y la forma en que divergen estas
    cosas es ofreciendo un acto que el backend va a negar — o escondiendo uno
    que sí se puede, que falla en silencio.

    Ojo: esto filtra la VISIBILIDAD por el permiso declarado del procedimiento.
    La autorización real la sigue resolviendo cada paso contra el gateway con el
    token de la persona; que un procedimiento se vea no garantiza que sus tres
    pasos vayan a pasar.
    """
    from app.tools.catalog import puede_usar

    return [
        p
        for p in PROCEDIMIENTOS
        if puede_usar(p, permissions, is_root=is_root, levels=levels)
    ]
