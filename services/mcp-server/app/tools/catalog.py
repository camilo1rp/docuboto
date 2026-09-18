# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Catálogo declarativo de tools MCP (E18, ADR-019).

Cada tool mapea 1:1 a un endpoint REST estable del api-gateway. El dispatcher
genérico traduce la invocación a una llamada HTTP. Añadir una tool = añadir una
entrada aquí (no código nuevo). `path` admite plantillas `{param}` que se rellenan
con los argumentos de la invocación; el resto de argumentos van a query (GET) o
body (escritura). `permission` (si se indica) filtra la visibilidad por los
permisos del usuario; `write=True` marca operaciones que exigen gate explícito.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class Param:
    """Un argumento de una tool, declarado UNA vez y consumido por cuatro sitios.

    Antes los parámetros vivían en `_TOOL_ARGS` (`services/assistant.py`), una
    tabla paralela mantenida a mano. Eso tenía dos costes reales, no teóricos:

    1. **Una tool sin entrada allí y sin parámetros de ruta se despachaba SIN
       ARGUMENTOS.** La whitelist del asistente intersecta con las propiedades
       declaradas; sin declaración, la intersección es vacía y la llamada salía
       con el cuerpo en blanco, sin que nada chillara.
    2. **Derivaba de la API sin que nadie lo notara.** Al migrar se encontró que
       `buscar_conocimiento` anunciaba al modelo `query` de mínimo 2 caracteres y
       `top_k` hasta 20, cuando el backend acepta 1 y 50 — y un test congelaba la
       divergencia.

    De aquí salen: el `input_schema` de Anthropic, el `inputSchema` de MCP, la
    whitelist de argumentos del dispatcher y las etiquetas de la tarjeta de
    confirmación. Una sola fuente para las cuatro.
    """

    name: str
    type: str = "string"  # string | integer | number | boolean | array | object
    required: bool = False
    # auto = ruta si aparece en la plantilla; si no, query en GET/DELETE y cuerpo
    # en el resto. Se declara explícito solo cuando el reparto no es evidente.
    location: str = "auto"
    description: str = ""  # para el modelo
    label: str = ""  # para la persona, en la tarjeta; por defecto, `name`
    enum: tuple[str, ...] | None = None
    format: str | None = None  # uuid | date | date-time
    min_length: int | None = None
    max_length: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    item_type: str | None = None  # cuando type == "array"
    # Ruido para la persona (paginación, tamaños): viaja al backend pero no se
    # pinta en la tarjeta de confirmación.
    show_in_card: bool = True

    @property
    def etiqueta(self) -> str:
        return self.label or self.name

    def json_schema(self) -> dict[str, Any]:
        """Fragmento JSON Schema de este parámetro."""
        esquema: dict[str, Any] = {"type": self.type}
        if self.description:
            esquema["description"] = self.description
        if self.enum is not None:
            esquema["enum"] = list(self.enum)
        if self.format is not None:
            esquema["format"] = self.format
        if self.min_length is not None:
            esquema["minLength"] = self.min_length
        if self.max_length is not None:
            esquema["maxLength"] = self.max_length
        if self.minimum is not None:
            esquema["minimum"] = self.minimum
        if self.maximum is not None:
            esquema["maximum"] = self.maximum
        if self.type == "array" and self.item_type:
            esquema["items"] = {"type": self.item_type}
        return esquema


class Invocable(Protocol):
    """Lo que necesita algo para ser INVOCABLE: una tool o un procedimiento.

    Se declara como Protocol —tipado estructural— y no como clase base porque
    `Tool` y `Procedimiento` no comparten implementación: una traduce a un
    endpoint y el otro encadena tools. Lo que sí comparten es el contrato que
    consumen la validación de argumentos, la tarjeta de confirmación y el gate
    de permisos, y ESE es el que hay que escribir una vez.

    Sin esto la alternativa era duplicar `coerce_args` y `card_fields` para
    procedimientos: dos validaciones que empiezan iguales y divergen al primer
    cambio, ofreciendo o negando actos según por dónde entre la llamada.
    """

    # Declarados como propiedades de SOLO LECTURA a propósito: `Tool` y
    # `Procedimiento` son dataclases congeladas, y un Protocol con atributos
    # simples exige que sean asignables — mypy rechaza entonces las dos clases
    # que precisamente cumplen el contrato. Nadie escribe estos campos: se
    # declaran una vez en el catálogo y no cambian en ejecución.
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def params(self) -> tuple[Param, ...]: ...

    @property
    def exactly_one_of(self) -> tuple[tuple[str, ...], ...]: ...

    @property
    def permission(self) -> str | None: ...

    @property
    def min_crud(self) -> int: ...

    @property
    def write(self) -> bool: ...

    @property
    def destructive(self) -> bool | None: ...

    @property
    def confirmar_con(self) -> str | None: ...

    @property
    def operacion(self) -> str: ...

    @property
    def params_by_name(self) -> dict[str, Param]: ...


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    method: str  # GET | POST
    path: str  # ruta gateway, con {params}
    path_params: tuple[str, ...] = ()
    permission: str | None = None  # permiso atómico requerido (None = lectura básica)
    # Nivel CRUD mínimo, en la escala REAL del sistema (auth-service
    # `migrations/tenant/001_rbac.sql`): 0=Ninguno 1=Leer 2=Editar 3=Crear/Borrar
    # 4=Nivel_1 5=Nivel_2. Las escrituras del sistema exigen 3, y el default 1
    # es el mismo de `require_permission`.
    #
    # Declararlo importa en los DOS sentidos: ofrecer una tool a quien solo tiene
    # nivel de lectura produce un 403 al ejecutarla, y exigir de más la esconde a
    # quien sí puede — el mismo defecto al revés, y ese falla en silencio.
    min_crud: int = 1
    write: bool = False  # operación de escritura
    # ¿El acto es irreversible o de alto impacto? Alimenta el `destructiveHint`
    # de MCP, con el que un cliente externo decide cuánta fricción poner antes de
    # ejecutar.
    #
    # El default es `None`, no `False`, **a propósito**: `None` significa «nadie
    # lo declaró», y el invariante de tests rechaza una tool de escritura que lo
    # deje sin declarar. Con default `False` la omisión pasaría por una
    # afirmación —«no es destructiva»— sin que nadie la hubiera hecho, que es la
    # forma en que un catálogo mantenido a mano se va volviendo mentira.
    destructive: bool | None = None
    # Qué hay que TECLEAR para llevar a cabo el acto, cuando la frase fija no
    # basta. Nombra un PARÁMETRO de la propia tool: la persona tiene que
    # reproducir su valor, y ese valor solo se conoce leyendo la tarjeta.
    #
    # La diferencia con `CONFIRMO` no es de severidad, es de OBJETO: una frase
    # fija confirma *un acto* y se puede teclear de memoria antes de leer nada;
    # reproducir el identificador del lote confirma **este** acto y no hay forma
    # de anticiparlo. Es lo que rompe el automatismo de confirmar sin mirar.
    #
    # Lo que NO hace, dicho aquí para que nadie lo suponga: no convierte a un
    # cliente MCP en una persona. Si quien teclea es el modelo, esto encarece el
    # descuido, no lo impide — por eso el acto que de verdad destruye no se
    # ofrece por este canal (ver `NO_OFRECIDAS` en `app/services/scope.py`).
    confirmar_con: str | None = None
    # El permiso REAL lo resuelve el servicio en tiempo de ejecución y no hay
    # decorador que leer. Hoy solo `ejecutar_transaccion`: cada tipo de
    # transacción declara el suyo en `transaction_types.requires_permission`, así
    # que gatear la ruta con un permiso fijo rompería a quien tiene `PERM_VOBO`
    # para dar vistos buenos y nada más.
    #
    # Marcarlo es la ÚNICA manera de que `shared/tests/test_permisos_declarados.py`
    # acepte que el catálogo exija un permiso que el decorador no pide — y ese
    # test comprueba además que la ruta marcada no tenga gate de decorador, para
    # que la marca no sirva de interruptor para silenciar un hallazgo real.
    permiso_en_runtime: bool = False
    # Grupos de argumentos de los que hay que enviar EXACTAMENTE UNO. Es una
    # restricción que existe en el backend —`AntecedentesRequest` la impone con
    # un `model_validator`— y que JSON Schema no expresa con las piezas que este
    # catálogo declara: haría falta un `oneOf`, otra forma de esquema.
    #
    # Sin declararla, los dos parámetros se anuncian como opcionales, el modelo
    # manda ambos o ninguno y el backend devuelve un 422 — el límite descubierto
    # fallando, que es exactamente lo que este trabajo viene a quitar de en
    # medio. Declarándola, `coerce_args` lo dice antes de que salga la petición.
    exactly_one_of: tuple[tuple[str, ...], ...] = ()
    epic: str = ""
    # Argumentos declarados. Fuente única: ver `Param`.
    params: tuple[Param, ...] = ()
    # El verbo de dominio que ve la PERSONA en el marco de alcance («radicar un
    # documento», no `radicar_documento`). El marco de alcance existe para que
    # nadie descubra un límite fallando, y para eso tiene que hablar el idioma
    # del trabajo, no el del catálogo.
    operacion: str = ""
    # Vía alternativa para quien NO tiene el permiso: qué camino le queda. Es la
    # diferencia entre «no puede enviar» y «no puede enviar, pero puede trasladar
    # el radicado a correspondencia para que lo envíen».
    fallback: str = ""

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "method": self.method,
            "write": self.write,
            "epic": self.epic,
        }

    @property
    def params_by_name(self) -> dict[str, Param]:
        return {p.name: p for p in self.params}


# ---------------------------------------------------------------------------
# Catálogo
#
# Los parámetros de cada tool se VERIFICARON contra el router real que el
# dispatcher termina llamando, no se copiaron de la tabla anterior. Ese cruce
# encontró una deriva: `buscar_conocimiento` anunciaba al modelo límites más
# estrechos que los del backend (ver su entrada).
# ---------------------------------------------------------------------------

_P_DOCUMENT_ID = Param(
    name="document_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del radicado.",
    label="Radicado",
)
_P_EXPEDIENTE_ID = Param(
    name="expediente_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del expediente.",
    label="Expediente",
)
_P_SERIE_ID = Param(
    name="serie_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la serie o subserie de la TRD.",
    label="Serie TRD",
)
_P_BORRADOR_ID = Param(
    name="borrador_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del borrador (aún no tiene número de radicado).",
    label="Borrador",
)
_P_PAGE = Param(
    name="page",
    type="integer",
    minimum=1,
    description="Página, 1-indexada.",
    label="Página",
    show_in_card=False,
)


# El radicado como parámetro de RUTA (flujos: `/workflows/{radicado_id}/...`).
_P_RADICADO_ID = Param(
    name="radicado_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del radicado.",
    label="Radicado",
)
# El mismo dato en el CUERPO (`/workflows/assign` y `/transfer` no lo llevan en
# la ruta). Se declaran por separado porque `location` cambia a dónde va el
# valor, y un `auto` aquí lo mandaría a la query.
_P_RADICADO_ID_BODY = Param(
    name="radicado_id",
    required=True,
    location="body",
    format="uuid",
    description="UUID del radicado que se tramita.",
    label="Radicado",
)
_P_TRACKING_BODY = Param(
    name="tracking_number",
    required=True,
    location="body",
    min_length=1,
    max_length=50,
    description=(
        "Número del radicado. Lo exige el servicio de flujos y queda en la hoja "
        "de ruta; sácalo del propio radicado, no lo compongas."
    ),
    label="Número de radicado",
)
# `action` lo sobreescribe el propio router (`model_copy(update=...)`), pero el
# schema lo declara obligatorio: sin enviarlo, Pydantic rechaza el cuerpo antes
# de llegar ahí. Se fija con un `enum` de un solo valor para que el modelo no
# tenga que adivinarlo ni pueda equivocarse.
_P_ACTION_ASSIGN = Param(
    name="action",
    required=True,
    enum=("assign",),
    description='Siempre "assign".',
    label="Acción",
    show_in_card=False,
)
_P_ACTION_TRANSFER = Param(
    name="action",
    required=True,
    enum=("transfer",),
    description='Siempre "transfer".',
    label="Acción",
    show_in_card=False,
)


_P_UNIDAD_ID = Param(
    name="unidad_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la unidad de conservación.",
    label="Unidad",
)
_P_TRANSFERENCIA_ID = Param(
    name="transferencia_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la transferencia.",
    label="Transferencia",
)


_P_LOTE_ID = Param(
    name="lote_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del lote de eliminación.",
    label="Lote",
)


_P_TRD_CODE = Param(
    name="code",
    required=True,
    location="path",
    max_length=50,
    description="Código de la serie TRD (por ejemplo DEM-100).",
    label="Serie",
)
_P_TVD_CODE = Param(
    name="code",
    required=True,
    location="path",
    max_length=50,
    description="Código de la agrupación TVD.",
    label="Agrupación",
)
_P_VERSION = Param(
    name="version",
    type="integer",
    required=True,
    location="path",
    minimum=1,
    description="Número de versión sobre la que se actúa.",
    label="Versión",
)


_P_AGRUPACION_ID = Param(
    name="agrupacion_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la agrupación de la TVD.",
    label="Agrupación",
)
_P_TIPO_ID = Param(
    name="tipo_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del tipo documental.",
    label="Tipo documental",
)


_P_PLAN_ID = Param(
    name="plan_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del plan PINAR.",
    label="Plan",
)
_P_PROYECTO_ID = Param(
    name="proyecto_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del proyecto del PINAR.",
    label="Proyecto",
)


# ── F12: identidad y estructura orgánica ────────────────────────────────────
_P_DEPENDENCIA_ID = Param(
    name="dependencia_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la dependencia.",
    label="Dependencia",
)
_P_GROUP_ID = Param(
    name="group_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del grupo (rol).",
    label="Grupo",
)
_P_USER_ID = Param(
    name="user_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la persona.",
    label="Persona",
)


# ── F10/F13: correo institucional y cola de eventos ─────────────────────────
_P_CUENTA_ID = Param(
    name="cuenta_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del buzón de correo.",
    label="Buzón",
)
_P_MENSAJE_ID = Param(
    name="mensaje_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID del correo en la bandeja.",
    label="Correo",
)
_P_EVENT_ID = Param(
    name="event_id",
    required=True,
    location="path",
    description="Identificador del evento en la cola de fallos.",
    label="Evento",
)


# ── Firma en cadena (E06) ───────────────────────────────────────────────────
_P_SOLICITUD_ID = Param(
    name="solicitud_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la solicitud de firma (la cadena, no el documento).",
    label="Cadena de firmas",
)
_P_FIRMA_ID = Param(
    name="firma_id",
    required=True,
    location="path",
    format="uuid",
    description="UUID de la firma producida.",
    label="Firma",
)
# El tipo de objeto va acotado al conjunto que el backend admite
# (`CADENA_OBJETO_TIPOS`). Declararlo como enum y no como texto libre no es
# cosmética: ahí el nivel de seguridad de la cadena se resolvía comparando este
# campo contra un literal, y `documento` estuvo admitido sin productor —una
# cadena que se creaba y no se podía firmar nunca— hasta que se retiró.
_P_OBJETO_TIPO = Param(
    name="objeto_tipo",
    required=True,
    enum=("radicado", "expediente", "borrador"),
    description="Qué clase de documento se firma.",
    label="Tipo de objeto",
)
_P_OBJETO_ID = Param(
    name="objeto_id",
    required=True,
    description=(
        "Identificador del documento. Admite el UUID o la identidad natural "
        "—el número de radicado, el código del expediente—."
    ),
    label="Documento",
)


def _p_size(maximo: int) -> Param:
    return Param(
        name="size",
        type="integer",
        minimum=1,
        maximum=maximo,
        description=f"Tamaño de página (máx. {maximo}).",
        label="Tamaño de página",
        show_in_card=False,
    )


CATALOG: tuple[Tool, ...] = (
    Tool(
        name="radicar_documento",
        description=(
            "Radica (registra) un documento de Entrada/Salida/Interno y devuelve su "
            "número de radicado. El número es INMUTABLE y no se reutiliza."
        ),
        method="POST",
        path="/api/v1/documents",
        # `PERM_RADI`, no `RADI_CREAR`. El permiso que este catálogo declaraba
        # NO EXISTE en el sistema: no está en ninguna de las cinco migraciones
        # que siembran `auth_permissions`, y solo aparecía aquí, en los tests de
        # este servicio —que lo consagraban como contrato— y en la documentación
        # de integraciones. `POST /api/v1/documents` exige `PERM_RADI`
        # (`document-service/app/routers/documents.py:148`).
        #
        # Efecto de la cadena inventada: `visible_tools` comparaba contra un
        # nombre que nunca está en el conjunto devuelto por `/auth/me`, así que
        # para todo usuario no-ROOT esta tool era invisible y su invocación
        # devolvía 403. La única escritura del MCP llevaba apagada desde su
        # primer commit. Ver `tests/test_catalog_invariants.py`, que ahora cruza
        # el catálogo contra los permisos reales para que no vuelva a pasar.
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        # Consume un número de radicado, que es inmutable y no se reutiliza: el
        # acto no se deshace, aunque no borre nada. `destructiveHint` pregunta si
        # el efecto es irreversible, no si destruye datos.
        destructive=True,
        epic="E01",
        operacion="Radicar un documento de entrada, salida o interno",
        # Los tres tipos exigen el MISMO permiso: `PERM_RADI_ENTRADA` y
        # `PERM_RADI_INTERNO` están en el catálogo de permisos y ningún gate los
        # usa. Por eso la operación no promete «solo entradas»: sería una
        # precisión que el sistema no tiene.
        fallback=(
            "Sin permiso de radicación, el documento puede entregarse a la "
            "dependencia de correspondencia para que lo radique."
        ),
        # Subconjunto de `DocumentCreate` (document-service/app/schemas/document.py).
        # Se omiten a propósito los campos de clasificación
        # (`nivel_seguridad`, `clasificacion_*`): clasificar un documento es un acto
        # motivado con su propio permiso, no un argumento más de la radicación.
        params=(
            Param(
                name="doc_type",
                required=True,
                enum=("E", "S", "I"),
                description="Tipo: E=Entrada, S=Salida, I=Interno.",
                label="Tipo",
            ),
            Param(
                name="subject",
                required=True,
                min_length=1,
                max_length=2000,
                description="Asunto del documento.",
                label="Asunto",
            ),
            Param(
                name="doc_class",
                max_length=100,
                description="Tipo documental según la TRD (p. ej. 'Derechos de Petición').",
                label="Tipo documental",
            ),
            Param(
                name="sender_name",
                max_length=255,
                description="Nombre de quien remite.",
                label="Remitente",
            ),
            Param(
                name="sender_entity",
                max_length=255,
                description="Entidad de quien remite.",
                label="Entidad remitente",
            ),
            Param(
                name="dest_dept_code",
                type="integer",
                minimum=1,
                maximum=999999,
                description="Código de la dependencia destino en el organigrama.",
                label="Dependencia destino",
            ),
            Param(
                name="origin_dept_code",
                type="integer",
                minimum=1,
                maximum=999999,
                description="Código de la dependencia origen.",
                label="Dependencia origen",
            ),
            Param(
                name="pages",
                type="integer",
                minimum=0,
                description="Número de folios.",
                label="Folios",
            ),
            Param(
                name="observations",
                description="Observaciones.",
                label="Observaciones",
            ),
            Param(
                name="response_days",
                type="integer",
                minimum=1,
                maximum=3650,
                description="Plazo de respuesta en días hábiles; si se omite, no hay plazo.",
                label="Plazo (días hábiles)",
            ),
        ),
    ),
    Tool(
        name="listar_tipos_documentales",
        description=(
            "Lista los tipos documentales de la TRD, opcionalmente los de una serie. "
            "Es de donde sale el nombre que espera `tipificar_radicado`."
        ),
        method="GET",
        path="/api/v1/tipos-documentales",
        epic="E04",
        operacion="Consultar los tipos documentales de la TRD",
        params=(
            Param(
                name="trd_serie_id",
                format="uuid",
                description="Filtra por la serie documental a la que pertenecen.",
                label="Serie",
            ),
            Param(
                name="activo",
                type="boolean",
                description="Filtra por tipos vigentes o retirados.",
                label="Solo vigentes",
            ),
        ),
    ),
    Tool(
        name="tipificar_radicado",
        description=(
            "Tipifica un radicado ya creado: le asigna o le corrige el tipo documental "
            "(`doc_class`) y, si ese tipo tiene plantilla de metadatos, sus campos "
            "variables. El tipo fija la serie TRD y con ella la retención del documento."
        ),
        method="PATCH",
        path="/api/v1/documents/{document_id}",
        path_params=("document_id",),
        permission="PERM_RADI",
        # `crud >= 3`, no el 1 del resto del PATCH. El endpoint deja corregir un
        # asunto con nivel de lectura, pero tipificar exige nivel de creación:
        # decide cuánto se conserva el documento y si acaba destruido (Ley
        # 594/2000 art. 24). Ofrecer la tool a quien solo alcanza el nivel 1
        # produciría un 403 al confirmar, que es el defecto que el marco de
        # alcance existe para evitar.
        min_crud=3,
        write=True,
        # Reversible: se puede volver a tipificar, y el asiento guarda el tipo
        # anterior. No por eso es inocuo —cambia la retención—, pero
        # `destructiveHint` pregunta por lo irreversible, y esto no lo es.
        destructive=False,
        epic="E03",
        operacion="Tipificar un radicado (asignar o corregir su tipo documental)",
        fallback=(
            "Sin nivel de creación en el permiso de radicación, el radicado puede "
            "trasladarse a la dependencia de correspondencia para que allí lo tipifiquen."
        ),
        params=(
            _P_DOCUMENT_ID,
            Param(
                name="doc_class",
                required=True,
                max_length=100,
                description=(
                    "Nombre del tipo documental, tal y como aparece en la TRD. "
                    "Consúltalo con `listar_tipos_documentales` antes de proponerlo: "
                    "un nombre inventado no falla, se guarda tal cual."
                ),
                label="Tipo documental",
            ),
            Param(
                name="metadata",
                type="object",
                description=(
                    "Campos variables del tipo documental. Se REEMPLAZAN enteros, no se "
                    "fusionan con los que hubiera. Si el tipo tiene plantilla activa, se "
                    "validan contra ella y el acto falla si no encajan."
                ),
                label="Metadatos del tipo",
            ),
        ),
    ),
    Tool(
        name="consultar_radicado",
        description="Consulta un radicado por su id.",
        method="GET",
        path="/api/v1/documents/{document_id}",
        path_params=("document_id",),
        epic="E01",
        operacion="Consultar un radicado",
        params=(_P_DOCUMENT_ID,),
    ),
    Tool(
        name="consultar_por_numero",
        description=(
            "Consulta un radicado por su número (p. ej. 2026-ICETEX-E-001234). "
            "Es como lo nombra la gente: el número va en el sello, el id no."
        ),
        method="GET",
        path="/api/v1/documents/by-tracking/{tracking_number}",
        path_params=("tracking_number",),
        epic="E01",
        operacion="Consultar un radicado por su número",
        params=(
            Param(
                name="tracking_number",
                required=True,
                location="path",
                max_length=64,
                description="Número de radicado completo, con sus guiones.",
                label="Número de radicado",
            ),
        ),
    ),
    Tool(
        name="listar_anexos",
        description=(
            "Lista los anexos de un radicado: nombre, tamaño, tipo y folios. "
            "Devuelve los datos del anexo, NO el fichero."
        ),
        method="GET",
        path="/api/v1/documents/{document_id}/anexos",
        path_params=("document_id",),
        epic="E07",
        operacion="Consultar los anexos de un radicado",
        params=(_P_DOCUMENT_ID,),
    ),
    Tool(
        name="consultar_disposicion",
        description=(
            "Consulta la disposición final del radicado: qué se hará con él al vencer "
            "su retención (conservar, eliminar, seleccionar) y si está marcado para "
            "eliminación."
        ),
        method="GET",
        path="/api/v1/documents/{document_id}/disposition",
        path_params=("document_id",),
        # El endpoint exige `USUA_PERM_EXPEDIENTE` a nivel 1. La ESCRITURA de la
        # disposición (nivel 3) no se expone: decide si un documento oficial se
        # conserva o se destruye, y eso no se hace conversando.
        permission="USUA_PERM_EXPEDIENTE",
        epic="E04",
        operacion="Consultar la disposición final de un radicado",
        params=(_P_DOCUMENT_ID,),
    ),
    Tool(
        name="actualizar_radicado",
        description=(
            "Corrige campos del radicado: asunto, dependencia destino, observaciones, "
            "estado y número de folios. El número de radicado es INMUTABLE y no se "
            "toca por aquí; para el tipo documental está `tipificar_radicado`."
        ),
        method="PATCH",
        path="/api/v1/documents/{document_id}",
        path_params=("document_id",),
        permission="PERM_RADI",
        # Nivel 1, que es el que el endpoint exige de verdad para estos campos.
        # Poner 3 «por prudencia» escondería la tool a quien sí puede corregir un
        # asunto: el mismo defecto al revés, y ese falla en silencio.
        min_crud=1,
        write=True,
        destructive=False,
        epic="E01",
        operacion="Corregir los datos de un radicado",
        fallback=(
            "Sin permiso de radicación, la corrección puede pedirse a la dependencia "
            "de correspondencia, que sí puede aplicarla."
        ),
        params=(
            _P_DOCUMENT_ID,
            Param(
                name="subject",
                min_length=1,
                max_length=2000,
                description="Asunto del radicado.",
                label="Asunto",
            ),
            Param(
                name="dest_dept",
                max_length=100,
                description="Nombre de la dependencia destino.",
                label="Dependencia destino",
            ),
            Param(
                name="observations",
                description="Observaciones.",
                label="Observaciones",
            ),
            Param(
                name="status",
                # Los cinco literales del schema MENOS `anulado`: anular es un acto
                # del circuito de anulación, con causal y aprobador, y el backend
                # tampoco lo admite por esta vía. Ofrecerlo aquí sugeriría que se
                # puede anular editando un campo.
                enum=("registered", "distributed", "in_progress", "closed"),
                description="Estado del trámite del radicado.",
                label="Estado",
            ),
            Param(
                name="pages",
                type="integer",
                minimum=0,
                description="Número de folios.",
                label="Folios",
            ),
        ),
    ),
    Tool(
        name="buscar_radicados",
        description="Búsqueda avanzada de radicados (texto + filtros).",
        method="GET",
        path="/api/v1/search",
        epic="E09",
        operacion="Buscar radicados",
        params=(
            Param(
                name="q",
                min_length=2,
                description="Texto a buscar (opcional; sin texto, la consulta se filtra por fecha).",
                label="Texto",
            ),
            Param(
                name="doc_type",
                enum=("E", "S", "I"),
                description="Tipo de radicado: Entrada, Salida o Interno.",
                label="Tipo",
            ),
            Param(name="status", description="Estado del radicado.", label="Estado"),
            Param(
                name="date_from",
                format="date",
                description="Fecha de registro desde (ISO 8601, incluida).",
                label="Desde",
            ),
            Param(
                name="date_to",
                format="date",
                description="Fecha de registro hasta (ISO 8601, incluida).",
                label="Hasta",
            ),
            Param(
                name="dest_dept_code",
                type="integer",
                description="Código de la dependencia destino.",
                label="Dependencia destino",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="crear_expediente",
        description=(
            "Abre un expediente: la agrupación lógica de los radicados de un mismo "
            "asunto o trámite. La serie TRD que se le asigne determina su retención."
        ),
        method="POST",
        path="/api/v1/expedientes",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E02",
        operacion="Abrir un expediente",
        fallback=(
            "Sin permiso de expedientes, puede pedirse la apertura a la dependencia "
            "que gestiona el archivo de la entidad."
        ),
        # Subconjunto de `ExpedienteCreate`. Se omiten a propósito:
        # `nivel_seguridad` (clasificar es un acto motivado con su propio permiso)
        # y el trío de fondo acumulado `origen`/`fecha_extrema_*`, que amarra el
        # expediente a una TVD y solo se fija en la creación, sin ruta de vuelta
        # (ADR-026): un valor equivocado dicho de palabra no se corrige después.
        params=(
            Param(
                name="name",
                required=True,
                min_length=1,
                max_length=500,
                description="Nombre del expediente.",
                label="Nombre",
            ),
            Param(
                name="description",
                description="Descripción del asunto que agrupa.",
                label="Descripción",
            ),
            Param(
                name="trd_serie_id",
                format="uuid",
                description=(
                    "Serie documental de la TRD. Consúltala con `listar_trd`: "
                    "de ella cuelgan la retención y la disposición final."
                ),
                label="Serie TRD",
            ),
            Param(
                name="metadata",
                type="object",
                description=(
                    "Metadatos variables del expediente. Si la serie tiene plantilla "
                    "activa, se validan contra ella."
                ),
                label="Metadatos",
            ),
        ),
    ),
    Tool(
        name="buscar_expedientes",
        description="Búsqueda de expedientes por texto, estado o fechas.",
        method="GET",
        path="/api/v1/expedientes/search",
        permission="USUA_PERM_EXPEDIENTE",
        epic="E09",
        operacion="Buscar expedientes",
        params=(
            Param(
                name="q",
                min_length=2,
                description="Texto a buscar (opcional).",
                label="Texto",
            ),
            Param(name="status", description="Estado del expediente.", label="Estado"),
            Param(
                name="date_from",
                format="date",
                description="Fecha de apertura desde (ISO 8601, incluida).",
                label="Desde",
            ),
            Param(
                name="date_to",
                format="date",
                description="Fecha de apertura hasta (ISO 8601, incluida).",
                label="Hasta",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="vincular_radicado",
        description=(
            "Vincula un radicado a un expediente: lo incorpora a su foliación y al "
            "índice electrónico."
        ),
        method="POST",
        path="/api/v1/expedientes/{expediente_id}/radicados",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=2,
        write=True,
        # Desvincular existe (`DELETE`, crud 3) pero NO se expone: rompe la
        # foliación. Que la vuelta atrás exista no hace inocua la ida, así que
        # esto no se marca como reversible a la ligera — se marca porque el acto
        # se corrige, no porque no importe.
        destructive=False,
        epic="E02",
        operacion="Vincular un radicado a un expediente",
        fallback=(
            "Sin permiso de expedientes, el radicado puede trasladarse a la "
            "dependencia de archivo indicando a qué expediente corresponde."
        ),
        params=(
            _P_EXPEDIENTE_ID,
            Param(
                name="radicado_id",
                required=True,
                format="uuid",
                description="UUID del radicado que se vincula.",
                label="Radicado",
            ),
            Param(
                name="tracking_number",
                required=True,
                min_length=1,
                max_length=50,
                description=(
                    "Número del radicado. Lo exige el backend y viaja al índice "
                    "electrónico; sácalo del propio radicado, no lo compongas."
                ),
                label="Número de radicado",
            ),
            Param(
                name="folios",
                type="integer",
                minimum=0,
                description="Número de folios que aporta al expediente.",
                label="Folios",
            ),
        ),
    ),
    Tool(
        name="actualizar_expediente",
        description=(
            "Corrige el nombre, la descripción o la serie TRD de un expediente. "
            "NO cierra ni transfiere: esas son transiciones con su propio acto."
        ),
        method="PATCH",
        path="/api/v1/expedientes/{expediente_id}",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=2,
        write=True,
        destructive=False,
        epic="E02",
        operacion="Corregir los datos de un expediente",
        fallback="Sin permiso de expedientes, la corrección se pide a la dependencia de archivo.",
        # `ExpedienteUpdate` lleva `extra="forbid"`: cualquier clave de más
        # devuelve 422 antes de tocar la base. Por eso se declaran exactamente
        # sus tres campos y ni uno más — `status` está retirado a propósito allí,
        # porque era una ruta alterna al cierre sin auditoría ni disposición.
        params=(
            _P_EXPEDIENTE_ID,
            Param(name="name", description="Nombre del expediente.", label="Nombre"),
            Param(name="description", description="Descripción.", label="Descripción"),
            Param(
                name="trd_serie_id",
                format="uuid",
                description="Serie documental de la TRD.",
                label="Serie TRD",
            ),
        ),
    ),
    Tool(
        name="cerrar_expediente",
        description=(
            "CIERRA el expediente: materializa la disposición de la TRD, congela su "
            "índice electrónico y lo saca de la fase de gestión. No se reabre."
        ),
        method="POST",
        path="/api/v1/expedientes/{expediente_id}/close",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        # `crud >= 3`, como el endpoint. Es el único irreversible del catálogo, y
        # entra porque «archivar» era parte explícita de lo que se pidió poder
        # hacer por esta vía.
        min_crud=3,
        write=True,
        destructive=True,
        epic="E02",
        operacion="Cerrar un expediente (irreversible)",
        fallback=(
            "Sin nivel de disposición sobre expedientes, el cierre lo ejecuta la "
            "dependencia de archivo; puede solicitárselo indicando el expediente."
        ),
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="consultar_foliado",
        description="Foliación del expediente: sus radicados en orden, con folios.",
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/foliado",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        epic="E02",
        operacion="Consultar la foliación de un expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="hoja_ruta_expediente",
        description=(
            "Hoja de ruta del expediente: qué le ha pasado y cuándo, en orden y sin "
            "posibilidad de edición."
        ),
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/eventos",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        epic="E02",
        operacion="Consultar la hoja de ruta de un expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="consultar_expediente",
        description="Consulta un expediente por su id (incluye radicados vinculados).",
        method="GET",
        path="/api/v1/expedientes/{expediente_id}",
        path_params=("expediente_id",),
        # `USUA_PERM_EXPEDIENTE`, declarado. El catálogo decía «sin permiso» y
        # el endpoint sí lo exige: la tool se ofrecía a quien iba a recibir un
        # 403 al usarla. Es el reflejo exacto de `RADI_CREAR` —allí se escondía
        # algo utilizable, aquí se anunciaba algo inutilizable— y justo lo que el
        # marco de alcance existe para evitar. Lo caza ahora
        # `shared/tests/test_permisos_declarados.py`.
        permission="USUA_PERM_EXPEDIENTE",
        epic="E02",
        operacion="Consultar un expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="indice_expediente",
        description="Índice electrónico vigente de un expediente.",
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/indice",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        epic="E15",
        operacion="Consultar el índice electrónico de un expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="listar_trd",
        description="Lista las series/subseries de la Tabla de Retención Documental.",
        method="GET",
        path="/api/v1/trd",
        epic="E04",
        operacion="Consultar la Tabla de Retención Documental",
        params=(
            Param(
                name="is_active",
                type="boolean",
                description="Filtra por series activas o inactivas.",
                label="Solo activas",
            ),
            _P_PAGE,
            _p_size(200),
        ),
    ),
    Tool(
        name="pendientes_equipo",
        description=(
            "Pasos de trámite pendientes, filtrables por dependencia o por persona "
            "asignada. Es la vista del equipo; `bandeja_tramite` es la propia."
        ),
        method="GET",
        path="/api/v1/workflows/pending",
        epic="E05",
        operacion="Consultar los pendientes de trámite del equipo",
        params=(
            Param(name="dept", description="Filtra por dependencia.", label="Dependencia"),
            Param(
                name="assigned_to",
                format="uuid",
                description="Filtra por usuario asignado (UUID).",
                label="Asignado a",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="tipos_transaccion",
        description=(
            "Tipos de transacción de trámite disponibles y el permiso que exige cada "
            "uno. Consúltalo antes de proponer `ejecutar_transaccion`."
        ),
        method="GET",
        path="/api/v1/workflows/transaction-types",
        epic="E05",
        operacion="Consultar los tipos de transacción de trámite",
    ),
    Tool(
        name="hoja_ruta_radicado",
        description="Historia completa del trámite de un radicado: por dónde ha pasado.",
        method="GET",
        path="/api/v1/workflows/{radicado_id}/history",
        path_params=("radicado_id",),
        epic="E05",
        operacion="Consultar la hoja de ruta de un radicado",
        params=(_P_RADICADO_ID,),
    ),
    Tool(
        name="paso_actual",
        description="Paso de trámite activo de un radicado: dónde está y quién lo tiene.",
        method="GET",
        path="/api/v1/workflows/{radicado_id}/current",
        path_params=("radicado_id",),
        epic="E05",
        operacion="Consultar el paso de trámite activo de un radicado",
        params=(_P_RADICADO_ID,),
    ),
    Tool(
        name="asignar_radicado",
        description=(
            "Asigna un radicado a una dependencia (y opcionalmente a una persona) "
            "para su trámite."
        ),
        method="POST",
        path="/api/v1/workflows/assign",
        permission="PERM_TRAMITAR",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E05",
        operacion="Asignar un radicado a una dependencia",
        fallback=(
            "Sin permiso de trámite, el radicado puede dejarse en la bandeja de la "
            "dependencia para que quien tramita lo asigne."
        ),
        params=(
            _P_RADICADO_ID_BODY,
            _P_TRACKING_BODY,
            Param(
                name="to_dept",
                required=True,
                min_length=1,
                max_length=100,
                description="Dependencia destino.",
                label="Dependencia destino",
            ),
            Param(
                name="assigned_to",
                format="uuid",
                description="UUID de la persona a la que se asigna (opcional).",
                label="Asignado a",
            ),
            _P_ACTION_ASSIGN,
            Param(name="notes", description="Notas del traslado.", label="Notas"),
            Param(
                name="doc_type",
                enum=("E", "S", "I"),
                description="Tipo del radicado, para las bandejas tipadas.",
                label="Tipo",
                show_in_card=False,
            ),
        ),
    ),
    Tool(
        name="trasladar_radicado",
        description=(
            "Traslada un radicado de una dependencia a otra. A diferencia de asignar, "
            "exige indicar la dependencia de origen."
        ),
        method="POST",
        path="/api/v1/workflows/transfer",
        permission="PERM_TRAMITAR",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E05",
        operacion="Trasladar un radicado a otra dependencia",
        fallback=(
            "Sin permiso de trámite, puede pedirse el traslado a quien tramita en la "
            "dependencia actual."
        ),
        params=(
            _P_RADICADO_ID_BODY,
            _P_TRACKING_BODY,
            Param(
                name="from_dept",
                required=True,
                min_length=1,
                max_length=100,
                description=(
                    "Dependencia de origen. El servicio la EXIGE para trasladar; "
                    "omitirla falla."
                ),
                label="Dependencia origen",
            ),
            Param(
                name="to_dept",
                required=True,
                min_length=1,
                max_length=100,
                description="Dependencia destino.",
                label="Dependencia destino",
            ),
            Param(
                name="assigned_to",
                format="uuid",
                description="UUID de la persona a la que se asigna (opcional).",
                label="Asignado a",
            ),
            _P_ACTION_TRANSFER,
            Param(name="notes", description="Notas del traslado.", label="Notas"),
        ),
    ),
    Tool(
        name="completar_paso",
        description=(
            "Marca como completado un paso de trámite. OJO: el identificador es el "
            "del PASO, no el del radicado — sácalo de `paso_actual`."
        ),
        method="PATCH",
        path="/api/v1/workflows/{step_id}/complete",
        path_params=("step_id",),
        permission="PERM_TRAMITAR",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E05",
        operacion="Completar un paso de trámite",
        fallback="Sin permiso de trámite, el paso lo cierra quien lo tiene asignado.",
        params=(
            Param(
                name="step_id",
                required=True,
                location="path",
                format="uuid",
                # `{step_id}` NO es `{radicado_id}`: esta ruta cuelga de otra
                # entidad que el resto del router. Es el error más probable de
                # todo el bloque, y por eso el parámetro se llama como lo que es
                # en vez de heredar el común.
                description=(
                    "UUID del PASO de trámite (no del radicado). Se obtiene de "
                    "`paso_actual` o de `bandeja_tramite`."
                ),
                label="Paso de trámite",
            ),
            Param(name="notes", description="Notas de cierre del paso.", label="Notas"),
        ),
    ),
    Tool(
        name="ejecutar_transaccion",
        description=(
            "Ejecuta una transacción de trámite sobre un radicado (informar, visto "
            "bueno, marcar leído…). Cada tipo exige su propio permiso: consulta "
            "`tipos_transaccion` antes de proponerla."
        ),
        method="POST",
        path="/api/v1/workflows/{radicado_id}/transactions",
        path_params=("radicado_id",),
        # El permiso REAL depende del tipo y se resuelve en el servidor
        # (`transaction_types.requires_permission`), así que el catálogo no puede
        # declararlo por adelantado. Se declara el SUELO —el mismo que aplica el
        # servicio cuando el tipo no declara el suyo—, que es lo mínimo cierto
        # que se puede afirmar sin inventar precisión.
        permission="PERM_TRAMITAR",
        min_crud=1,
        write=True,
        destructive=False,
        permiso_en_runtime=True,
        epic="E05",
        operacion="Ejecutar una transacción de trámite",
        fallback=(
            "Cada transacción exige su permiso propio; sin él, puede solicitarse a "
            "quien lo tenga trasladándole el radicado."
        ),
        params=(
            _P_RADICADO_ID,
            Param(
                name="tipo_tx",
                required=True,
                min_length=1,
                max_length=40,
                description=(
                    "Código del tipo de transacción, tal cual lo devuelve "
                    "`tipos_transaccion`. Un código inventado devuelve 404."
                ),
                label="Transacción",
            ),
            Param(name="comentario", description="Comentario del acto.", label="Comentario"),
        ),
    ),
    Tool(
        name="bandeja_tramite",
        description="Bandeja de trámite (inbox) de flujos de trabajo.",
        method="GET",
        path="/api/v1/workflows/inbox",
        epic="E05",
        operacion="Consultar la bandeja de trámite",
        params=(
            Param(
                name="box",
                enum=("entrada", "salida", "internos"),
                description="Bandeja tipada.",
                label="Bandeja",
            ),
            Param(name="dept", description="Filtra por dependencia.", label="Dependencia"),
            Param(
                name="assigned_to",
                format="uuid",
                description="Filtra por usuario asignado (UUID).",
                label="Asignado a",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="consultar_fuid",
        description="FUID (inventario documental) de una transferencia.",
        method="GET",
        path="/api/v1/transferencias/{transferencia_id}/fuid",
        path_params=("transferencia_id",),
        permission="USUA_PERM_EXPEDIENTE",
        epic="E12",
        operacion="Consultar el inventario documental (FUID) de una transferencia",
        params=(
            Param(
                name="transferencia_id",
                required=True,
                location="path",
                format="uuid",
                description="UUID de la transferencia.",
                label="Transferencia",
            ),
        ),
    ),
    Tool(
        name="buscar_conocimiento",
        description=(
            "Búsqueda semántica sobre la base de conocimiento documental: devuelve fragmentos "
            "relevantes (con su fuente) para una consulta en lenguaje natural. Solo recupera "
            "fragmentos que el usuario tiene autorizado ver."
        ),
        method="POST",
        path="/api/v1/knowledge/search",
        permission=None,
        write=False,
        epic="E21",
        operacion="Buscar en la base de conocimiento documental",
        # `acl` NO se expone: el knowledge-service impone el techo de clearance
        # server-side desde `X-User-Id` (RF-SEG-08). Exponerlo al modelo sería
        # superficie de mando innecesaria.
        #
        # Los límites SE ALINEARON con el backend al migrar. La tabla anterior
        # anunciaba `query` de mínimo 2 caracteres y `top_k` hasta 20, mientras
        # `SearchRequest` (knowledge-service/app/routers/knowledge.py) acepta
        # `min_length=1` y `le=50` — y un test congelaba la divergencia. `top_k`
        # se deja en 20 A PROPÓSITO y ahora dicho en voz alta: es un límite del
        # asistente para no inundar la ventana de contexto del modelo, no del
        # sistema. `query` pasa a 1, que es lo que el sistema acepta.
        params=(
            Param(
                name="query",
                required=True,
                min_length=1,
                description=(
                    "Consulta en lenguaje natural para recuperar fragmentos relevantes "
                    "de la base de conocimiento."
                ),
                label="Consulta",
            ),
            Param(
                name="top_k",
                type="integer",
                minimum=1,
                maximum=20,
                description="Número máximo de fragmentos a devolver (5 si se omite).",
                label="Fragmentos",
                show_in_card=False,
            ),
        ),
    ),
    Tool(
        name="buscar_antecedentes",
        description=(
            "Antecedentes de un asunto: radicados anteriores parecidos, cada uno con SU "
            "NÚMERO para poder verificarlo en la fuente. Se pide de una de dos formas y "
            "solo una: por asunto ('query') o por radicado ('radicado_ref', el número de "
            "un radicado ya existente). Enviar las dos, o ninguna, es un error. "
            "No redacta ni escribe nada: solo recupera."
        ),
        method="POST",
        path="/api/v1/knowledge/antecedentes",
        permission=None,
        write=False,
        epic="E21",
        operacion="Buscar antecedentes de un asunto o de un radicado",
        exactly_one_of=(("query", "radicado_ref"),),
        # El XOR entre `query` y `radicado_ref` lo impone `AntecedentesRequest`
        # con un `model_validator`. Se declara con `exactly_one_of` para que
        # `coerce_args` lo rechace ANTES de salir: el 422 del backend dice lo
        # mismo, pero solo después de que el usuario haya esperado.
        #
        # `source_type` NO se expone: su único valor real hoy es el default
        # ("radicado"), y ofrecerlo como texto libre es superficie de mando que
        # solo sirve para equivocarse.
        params=(
            Param(
                name="query",
                min_length=2,
                description="Asunto o descripción del caso del que se buscan antecedentes.",
                label="Asunto",
            ),
            Param(
                name="radicado_ref",
                min_length=1,
                description=(
                    "Número de un radicado existente cuyos antecedentes se buscan. "
                    "El propio radicado no se devuelve como antecedente de sí mismo."
                ),
                label="Radicado de referencia",
            ),
            Param(
                name="top_k",
                type="integer",
                minimum=1,
                maximum=20,
                description="Número máximo de antecedentes a devolver (5 si se omite).",
                label="Antecedentes",
                show_in_card=False,
            ),
        ),
    ),
    Tool(
        name="redactar_con_antecedentes",
        description=(
            "Redacta un texto APOYADO en documentos del archivo y devuelve sus CITAS: "
            "cada afirmación con el radicado del que sale. NO escribe nada en el "
            "sistema — el texto se revisa y, si sirve, se lleva a un borrador con "
            "'crear_borrador'. Si la respuesta llega sin 'answer' o sin citas, el "
            "sistema no generó texto: entonces se ofrecen los antecedentes tal cual, "
            "no una redacción propia. El texto es generado y así hay que presentarlo."
        ),
        method="POST",
        path="/api/v1/knowledge/rag",
        permission=None,
        write=False,
        epic="E21",
        operacion="Redactar un texto apoyado en antecedentes, con citas",
        params=(
            Param(
                name="query",
                required=True,
                min_length=2,
                description="Lo que se quiere redactar o responder, en lenguaje natural.",
                label="Consulta",
            ),
            Param(
                name="top_k",
                type="integer",
                minimum=1,
                maximum=20,
                description="Número máximo de fragmentos de apoyo (5 si se omite).",
                label="Fragmentos",
                show_in_card=False,
            ),
        ),
    ),
    Tool(
        name="listar_respuestas",
        description=(
            "Salidas que responden a este radicado: qué se le ha contestado ya y cuándo. "
            "Es la comprobación previa a redactar otra respuesta."
        ),
        method="GET",
        path="/api/v1/documents/{document_id}/respuestas",
        path_params=("document_id",),
        # El endpoint no exige permiso atómico: acota por clearance (RF-SEG-08),
        # que se resuelve server-side y no es un permiso que el catálogo declare.
        permission=None,
        write=False,
        epic="E01",
        operacion="Ver qué respuestas se han dado a un radicado",
        params=(_P_DOCUMENT_ID,),
    ),
    Tool(
        name="crear_respuesta_vinculada",
        description=(
            "Crea una Salida en respuesta a un radicado de Entrada y la deja vinculada a "
            "él como antecedente. CONSUME un número de radicado, que es inmutable. "
            "Hereda el asunto del antecedente si no se indica otro."
        ),
        method="POST",
        path="/api/v1/documents/{document_id}/respuesta",
        path_params=("document_id",),
        permission="PERM_RESPUESTA_VINCULADA",
        min_crud=1,
        write=True,
        # Igual que `radicar_documento`: no borra nada y aun así no se deshace,
        # porque el número queda consumido. `destructiveHint` pregunta por lo
        # irreversible, no por lo que destruye.
        destructive=True,
        epic="E01",
        operacion="Responder un radicado con una Salida vinculada",
        fallback=(
            "Sin permiso de respuesta vinculada, la Salida puede prepararse como "
            "borrador y pedir a la dependencia competente que la radique."
        ),
        # Subconjunto de `RespuestaRapidaCreate` (document-service). Se declaran
        # todos sus campos: ninguno es obligatorio porque el asunto y la
        # dependencia se heredan del antecedente cuando se omiten.
        params=(
            _P_DOCUMENT_ID,
            Param(
                name="subject",
                max_length=2000,
                description="Asunto de la respuesta. Si se omite, hereda el del antecedente.",
                label="Asunto",
            ),
            Param(
                name="dest_dept",
                max_length=100,
                description="Dependencia destinataria.",
                label="Destino",
            ),
            Param(
                name="dest_dept_code",
                type="integer",
                minimum=1,
                maximum=999999,
                description="Código numérico de la dependencia destinataria.",
                label="Código de destino",
            ),
            Param(
                name="observations",
                description="Cuerpo u observaciones de la respuesta.",
                label="Observaciones",
            ),
            Param(
                name="response_days",
                type="integer",
                minimum=1,
                maximum=3650,
                description="Días para responder, si esta Salida a su vez espera respuesta.",
                label="Días de respuesta",
            ),
        ),
    ),
    Tool(
        name="listar_plantillas",
        description=(
            "Plantillas de redacción disponibles, con su identificador. Es de donde sale "
            "el 'plantilla_id' de 'crear_borrador': no lo inventes."
        ),
        method="GET",
        path="/api/v1/plantillas",
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Consultar las plantillas de redacción",
        params=(
            Param(
                name="activo",
                type="boolean",
                description="Solo plantillas activas (true) o solo retiradas (false).",
                label="Activas",
            ),
        ),
    ),
    Tool(
        name="listar_borradores",
        description=(
            "Borradores de Salida: los que están en redacción, los aprobados y los ya "
            "radicados. Un borrador NO tiene número de radicado hasta que se radica."
        ),
        method="GET",
        path="/api/v1/borradores",
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Ver los borradores de salida",
        params=(
            Param(
                name="estado",
                enum=("borrador", "aprobado", "radicado"),
                description="Filtra por estado del borrador.",
                label="Estado",
            ),
        ),
    ),
    Tool(
        name="crear_borrador",
        description=(
            "Crea un borrador de Salida o Interno: texto que todavía NO es un radicado y "
            "NO consume número. Se puede editar cuantas veces haga falta y solo al "
            "radicarlo se convierte en documento oficial."
        ),
        method="POST",
        path="/api/v1/borradores",
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        # Un borrador no es un acto de archivo: no tiene número, no entra en el
        # índice y se edita o se abandona sin consecuencia. Es justamente la
        # pieza que permite proponer sin comprometer.
        destructive=False,
        epic="E01",
        operacion="Preparar un borrador de salida",
        fallback=(
            "Sin permiso de radicación, el texto puede entregarse a la dependencia "
            "de correspondencia para que lo prepare."
        ),
        params=(
            Param(
                name="doc_type",
                enum=("S", "I"),
                description="S = Salida, I = Interno. Salida si se omite.",
                label="Tipo",
            ),
            Param(
                name="subject",
                required=True,
                min_length=1,
                max_length=2000,
                description="Asunto del borrador.",
                label="Asunto",
            ),
            Param(
                name="dest_dept",
                max_length=100,
                description="Dependencia destinataria.",
                label="Destino",
            ),
            Param(
                name="cuerpo",
                description=(
                    "Texto del documento. Si se omite y se indica una plantilla, el "
                    "cuerpo sale de la plantilla."
                ),
                label="Cuerpo",
            ),
            Param(
                name="plantilla_id",
                format="uuid",
                description="Plantilla de la que copiar el cuerpo (de 'listar_plantillas').",
                label="Plantilla",
            ),
        ),
    ),
    Tool(
        name="editar_borrador",
        description=(
            "Corrige el asunto, el destino o el cuerpo de un borrador. Deja de admitirse "
            "una vez el borrador se ha radicado."
        ),
        method="PATCH",
        path="/api/v1/borradores/{borrador_id}",
        path_params=("borrador_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E01",
        operacion="Corregir un borrador de salida",
        fallback="Sin permiso de radicación, la corrección se pide a quien preparó el borrador.",
        # Exactamente los tres campos de `BorradorUpdate`. `doc_type` y
        # `plantilla_id` NO se editan allí: el tipo documental de una Salida se
        # decide al crearla.
        params=(
            _P_BORRADOR_ID,
            Param(
                name="subject",
                max_length=2000,
                description="Asunto del borrador.",
                label="Asunto",
            ),
            Param(
                name="dest_dept",
                max_length=100,
                description="Dependencia destinataria.",
                label="Destino",
            ),
            Param(name="cuerpo", description="Texto del documento.", label="Cuerpo"),
        ),
    ),
    Tool(
        name="aprobar_borrador",
        description=(
            "Aprueba el borrador. Es REQUISITO para radicarlo: un borrador sin "
            "aprobar no se puede radicar. Es el paso previo al acto que consume el "
            "número."
        ),
        method="POST",
        path="/api/v1/borradores/{borrador_id}/aprobar",
        path_params=("borrador_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        # El borrador aprobado se sigue editando (`_require_editable` solo cierra
        # el estado `radicado`), así que la marca se pone y se quita de hecho al
        # seguir trabajando: no es un acto irreversible.
        #
        # La descripción decía que la aprobación NO era requisito para radicar, y
        # era cierto: el servicio solo comprobaba que no estuviera ya radicado.
        # Se cerró ese hueco —la exigencia vivía únicamente en el navegador— y
        # con él se corrige aquí la frase, que si no quedaría afirmando lo
        # contrario de lo que hace el backend.
        destructive=False,
        epic="E01",
        operacion="Aprobar un borrador de salida",
        fallback="Sin permiso de radicación, la aprobación la da la dependencia competente.",
        params=(_P_BORRADOR_ID,),
    ),
    Tool(
        name="radicar_borrador",
        description=(
            "Convierte el borrador en un radicado real: le asigna número, que es "
            "INMUTABLE y no se reutiliza. Exige que el borrador esté APROBADO. A partir "
            "de aquí ya no se edita: es el acto que cierra la redacción, no un guardado más."
        ),
        method="POST",
        path="/api/v1/borradores/{borrador_id}/radicar",
        path_params=("borrador_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=True,
        epic="E01",
        operacion="Radicar un borrador (le asigna número definitivo)",
        fallback=(
            "Sin permiso de radicación, el borrador queda preparado y lo radica la "
            "dependencia de correspondencia."
        ),
        params=(_P_BORRADOR_ID,),
    ),
    Tool(
        name="consultar_serie",
        description=(
            "Una serie o subserie de la TRD: su código, su nombre y los tiempos de "
            "retención y disposición que gobiernan a todo lo que se clasifique en ella."
        ),
        method="GET",
        path="/api/v1/trd/{serie_id}",
        path_params=("serie_id",),
        permission=None,
        write=False,
        epic="E04",
        operacion="Consultar una serie de la TRD",
        params=(_P_SERIE_ID,),
    ),
    Tool(
        name="consultar_retencion",
        description=(
            "Calcula, para una serie de la TRD y una fecha de cierre, cuándo termina cada "
            "fase del archivo y qué disposición final corresponde. Es un CÁLCULO: no "
            "cambia nada y no sustituye a la disposición registrada del expediente."
        ),
        method="GET",
        path="/api/v1/trd/{serie_id}/retention",
        path_params=("serie_id",),
        permission=None,
        write=False,
        epic="E04",
        operacion="Calcular el calendario de retención de una serie",
        params=(
            _P_SERIE_ID,
            Param(
                name="closed_at",
                required=True,
                format="date",
                description=(
                    "Fecha de cierre del expediente (AAAA-MM-DD), desde la que cuenta el "
                    "calendario. Es la fecha real de cierre, no la de hoy."
                ),
                label="Fecha de cierre",
            ),
        ),
    ),
    Tool(
        name="ubicacion_expediente",
        description=(
            "DÓNDE está físicamente un expediente: sus unidades de conservación con la "
            "signatura topográfica. Dice dónde está, no qué contiene."
        ),
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/unidades",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E17",
        operacion="Localizar físicamente un expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="listar_prestamos",
        description=(
            "Préstamos de unidades del archivo físico: qué está prestado, a quién y desde "
            "cuándo. Con 'estado' se ven solo los vencidos, que es la consulta habitual."
        ),
        method="GET",
        path="/api/v1/prestamos",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E17",
        operacion="Consultar los préstamos del archivo físico",
        params=(
            Param(
                name="estado",
                enum=("prestado", "devuelto", "vencido"),
                description="Filtra por estado del préstamo.",
                label="Estado",
            ),
        ),
    ),
    # ── F1 Ventanilla y radicación ──────────────────────────────────────
    #
    # Lo que faltaba del flujo que más se usa. Los permisos NO se declararon a
    # ojo: se extrajeron de los `require_permission` de cada router, porque un
    # permiso inventado hace la tool invisible para todo el mundo salvo ROOT y
    # el fallo no se nota hasta que alguien la pide (ya pasó con `RADI_CREAR`).
    Tool(
        name="listar_radicados",
        description=(
            "Lista los radicados del tenant, con filtros por tipo y estado. Es la "
            "puerta de entrada cuando no se conoce el número: para buscar por "
            "contenido está `buscar_documentos`."
        ),
        method="GET",
        path="/api/v1/documents",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Listar radicados",
        params=(
            Param(
                name="doc_type",
                enum=("E", "S", "I"),
                description="Tipo: E=Entrada, S=Salida, I=Interno.",
                label="Tipo",
            ),
            Param(name="status", description="Estado del radicado.", label="Estado"),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="buscar_terceros",
        description=(
            "Busca en el catálogo de terceros (ciudadanos, empresas, entidades). "
            "**Búscalo SIEMPRE antes de registrar uno nuevo**: la plaga del sistema "
            "de origen fueron treinta copias del mismo señor, y no se crearon por "
            "saltarse una restricción sino por no encontrar lo que ya estaba."
        ),
        method="GET",
        path="/api/v1/terceros",
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Buscar un tercero en el catálogo",
        params=(
            Param(
                name="q",
                description="Texto a buscar: nombre, entidad o documento de identidad.",
                label="Búsqueda",
            ),
            Param(
                name="tipo",
                enum=("ciudadano", "empresa", "entidad_publica", "usuario_interno", "direccion_libre"),
                description="Tipología del tercero.",
                label="Tipo",
            ),
            Param(
                name="limit",
                type="integer",
                minimum=1,
                maximum=100,
                description="Máximo de resultados.",
                label="Máximo",
                show_in_card=False,
            ),
        ),
    ),
    Tool(
        name="registrar_tercero",
        description=(
            "Registra un tercero. Es IDEMPOTENTE por documento de identidad: pedirlo "
            "dos veces devuelve el que ya existe, no crea otro. El municipio se "
            "aporta por su código DIVIPOLA (`municipio_codigo`) y el sistema deriva "
            "el departamento y el nombre oficial — escribirlo a mano es lo que "
            "convierte «Bogotá», «BOGOTA» y «Bogota D.C.» en tres municipios."
        ),
        method="POST",
        path="/api/v1/terceros",
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E01",
        operacion="Registrar un tercero en el catálogo",
        params=(
            Param(
                name="tipo",
                required=True,
                enum=("ciudadano", "empresa", "entidad_publica", "usuario_interno", "direccion_libre"),
                description="Tipología del tercero.",
                label="Tipo",
            ),
            Param(name="nombre", required=True, max_length=255, description="Nombre o razón social.", label="Nombre"),
            Param(name="entidad", max_length=255, description="Entidad a la que pertenece.", label="Entidad"),
            Param(name="tipo_documento", max_length=10, description="CC, NIT, CE, PA…", label="Tipo de documento"),
            Param(
                name="numero_documento",
                max_length=50,
                description="Número del documento de identidad.",
                label="Documento",
            ),
            Param(name="email", max_length=255, description="Correo electrónico.", label="Correo"),
            Param(name="telefono", max_length=50, description="Teléfono de contacto.", label="Teléfono"),
            Param(name="direccion", max_length=500, description="Dirección física.", label="Dirección"),
            Param(
                name="municipio_codigo",
                max_length=5,
                description="Código DIVIPOLA del municipio (5 dígitos). El departamento se deriva de él.",
                label="Municipio (DIVIPOLA)",
            ),
        ),
    ),
    Tool(
        name="listar_departamentos",
        description="Departamentos del catálogo oficial DIVIPOLA (DANE), para la cascada geográfica.",
        method="GET",
        path="/api/v1/terceros/divipola/departamentos",
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Consultar los departamentos DIVIPOLA",
        params=(),
    ),
    Tool(
        name="listar_municipios",
        description=(
            "Municipios de un departamento (DIVIPOLA). El departamento es obligatorio: "
            "el catálogo nacional tiene 1.122 entidades y devolverlo entero no ayuda a nadie."
        ),
        method="GET",
        path="/api/v1/terceros/divipola/municipios",
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Consultar los municipios de un departamento",
        params=(
            Param(
                name="departamento",
                required=True,
                min_length=2,
                max_length=2,
                description="Código DIVIPOLA del departamento (2 dígitos).",
                label="Departamento",
            ),
            Param(name="q", description="Filtra por nombre (sin distinguir tildes).", label="Búsqueda"),
        ),
    ),
    Tool(
        name="consultar_actores",
        description=(
            "Destinatarios y remitentes de un radicado, con el contacto CONGELADO en el "
            "momento del envío. No es la ficha viva del tercero: dice a dónde se envió "
            "de verdad, y no cambia aunque la ficha se actualice después."
        ),
        method="GET",
        path="/api/v1/terceros/radicado/{radicado_id}/actores",
        path_params=("radicado_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Consultar los actores de un radicado",
        params=(_P_RADICADO_ID,),
    ),
    Tool(
        name="reclasificar_radicado",
        description=(
            "Cambia el nivel de seguridad de un radicado (pública, reservada, clasificada). "
            "Exige MOTIVO y, al clasificar, el fundamento jurídico: reservar información "
            "pública es un acto administrativo, no una etiqueta (Ley 1712/2014)."
        ),
        method="PATCH",
        path="/api/v1/documents/{document_id}/security-level",
        path_params=("document_id",),
        permission="PERM_RECLASIFICAR",
        min_crud=3,
        write=True,
        # Cambia quién puede ver el documento a partir de ya: no destruye, pero
        # no se «deshace» sin otro acto motivado y con su propio asiento.
        destructive=True,
        epic="E08",
        operacion="Reclasificar el nivel de seguridad de un radicado",
        params=(
            _P_DOCUMENT_ID,
            Param(
                name="nivel_seguridad",
                type="integer",
                required=True,
                minimum=1,
                maximum=3,
                description="1=Pública, 2=Reservada, 3=Clasificada.",
                label="Nivel",
            ),
            Param(
                name="motivo",
                required=True,
                min_length=1,
                max_length=500,
                description="Por qué se reclasifica. Queda en el asiento de auditoría.",
                label="Motivo",
            ),
            Param(name="causal_reserva", description="Causal de reserva invocada.", label="Causal"),
            Param(
                name="fundamento_juridico",
                description="Norma que ampara la reserva (obligatoria al clasificar).",
                label="Fundamento",
            ),
            Param(
                name="plazo_reserva_meses",
                type="integer",
                description="Plazo de la reserva en meses.",
                label="Plazo (meses)",
            ),
        ),
    ),
    Tool(
        name="publicar_de_oficio",
        description=(
            "Publica un radicado en el portal de transparencia (Ley 1712/2014 art. 12) "
            "o retira su publicación. Exige fundamento: publicar de oficio es una "
            "decisión motivada."
        ),
        method="POST",
        path="/api/v1/documents/{document_id}/publicacion",
        path_params=("document_id",),
        permission="PERM_RECLASIFICAR",
        min_crud=3,
        write=True,
        destructive=True,
        epic="E13",
        operacion="Publicar de oficio un radicado",
        params=(
            _P_DOCUMENT_ID,
            Param(
                name="publicar",
                type="boolean",
                required=True,
                description="true publica, false retira la publicación.",
                label="Publicar",
            ),
            Param(
                name="fundamento",
                required=True,
                min_length=1,
                max_length=500,
                description="Fundamento de la decisión de publicar o de retirar.",
                label="Fundamento",
            ),
        ),
    ),
    Tool(
        name="solicitar_anulacion",
        description=(
            "Solicita la anulación de un radicado (paso 1 de 2). NO lo anula: la decide "
            "después un supervisor. Sobre una Entrada la anulación **no existe** como "
            "acto —el sistema responde 409— porque no se puede negar haber recibido algo."
        ),
        method="POST",
        path="/api/v1/documents/{document_id}/anulacion",
        path_params=("document_id",),
        permission="PERM_SOL_ANULAR",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E01",
        operacion="Solicitar la anulación de un radicado",
        params=(
            _P_DOCUMENT_ID,
            Param(
                name="causal",
                required=True,
                min_length=1,
                max_length=15,
                description="Código de la causal de anulación.",
                label="Causal",
            ),
            Param(name="motivo", max_length=2000, description="Explicación de la solicitud.", label="Motivo"),
        ),
    ),
    Tool(
        name="consultar_anulacion",
        description="Estado de la solicitud de anulación de un radicado, si la tiene.",
        method="GET",
        path="/api/v1/documents/{document_id}/anulacion",
        path_params=("document_id",),
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Consultar la anulación de un radicado",
        params=(_P_DOCUMENT_ID,),
    ),
    Tool(
        name="cola_anulaciones",
        description=(
            "Solicitudes de anulación pendientes de decidir, la más antigua primero. "
            "Mismo permiso que decidirlas: ofrecer una cola que luego rechaza cada acto "
            "sería peor que no mostrarla."
        ),
        method="GET",
        path="/api/v1/anulaciones",
        permission="PERM_PANU_CODI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Consultar la cola de anulaciones pendientes",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="aprobar_anulacion",
        description=(
            "Aprueba la anulación de un radicado (paso 2; supervisor). El radicado queda "
            "ANULADO: conserva su número —que no se reutiliza— y deja de surtir efectos. "
            "Quien solicitó no puede aprobar su propia solicitud."
        ),
        method="POST",
        path="/api/v1/documents/{document_id}/anulacion/aprobar",
        path_params=("document_id",),
        permission="PERM_PANU_CODI",
        min_crud=1,
        write=True,
        destructive=True,
        epic="E01",
        operacion="Aprobar la anulación de un radicado",
        params=(
            _P_DOCUMENT_ID,
            Param(name="observacion", max_length=2000, description="Observación de la decisión.", label="Observación"),
        ),
    ),
    Tool(
        name="rechazar_anulacion",
        description="Rechaza la solicitud de anulación (paso 2; supervisor). El radicado sigue vigente.",
        method="POST",
        path="/api/v1/documents/{document_id}/anulacion/rechazar",
        path_params=("document_id",),
        permission="PERM_PANU_CODI",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E01",
        operacion="Rechazar la anulación de un radicado",
        params=(
            _P_DOCUMENT_ID,
            Param(name="observacion", max_length=2000, description="Motivo del rechazo.", label="Observación"),
        ),
    ),
    # ── F2 Trámite y reparto ────────────────────────────────────────────
    Tool(
        name="devolver_radicado",
        description=(
            "Devuelve el radicado a la dependencia de origen con una causal. Es el acto "
            "que dice «esto no me corresponde» o «falta un requisito», y queda en la hoja "
            "de ruta: devolver no es lo mismo que trasladar, y el archivo tiene que poder "
            "distinguirlos."
        ),
        method="POST",
        path="/api/v1/workflows/{radicado_id}/devolver",
        path_params=("radicado_id",),
        permission="PERM_TRAMITAR",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E05",
        operacion="Devolver un radicado con causal",
        params=(
            _P_RADICADO_ID,
            Param(
                name="to_dept",
                required=True,
                min_length=1,
                max_length=100,
                description="Dependencia a la que se devuelve.",
                label="Devolver a",
            ),
            Param(
                name="causal",
                required=True,
                min_length=1,
                max_length=15,
                description="Código de la causal de devolución.",
                label="Causal",
            ),
            Param(name="comentario", description="Explicación para quien lo recibe.", label="Comentario"),
        ),
    ),
    Tool(
        name="crear_cadena_vobo",
        description=(
            "Crea la cadena de vistos buenos de un radicado: quiénes deben revisarlo y "
            "EN QUÉ ORDEN. El orden es el punto — una cadena sin orden no es una cadena, "
            "y el siguiente revisor solo puede decidir cuando le llega el turno."
        ),
        method="POST",
        path="/api/v1/workflows/{radicado_id}/vistos-buenos",
        path_params=("radicado_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E05",
        operacion="Crear la cadena de vistos buenos",
        params=(
            _P_RADICADO_ID,
            Param(
                name="revisores",
                type="array",
                item_type="string",
                required=True,
                description="UUID de cada revisor, EN el orden en que deben revisar.",
                label="Revisores",
            ),
        ),
    ),
    Tool(
        name="consultar_cadena_vobo",
        description="Cadena de vistos buenos de un radicado: quién ha decidido y de quién es el turno.",
        method="GET",
        path="/api/v1/workflows/{radicado_id}/vistos-buenos",
        path_params=("radicado_id",),
        min_crud=1,
        write=False,
        epic="E05",
        operacion="Consultar la cadena de vistos buenos",
        params=(_P_RADICADO_ID,),
    ),
    Tool(
        name="decidir_visto_bueno",
        description=(
            "Da o niega el visto bueno como revisor de turno. Solo puede hacerlo quien "
            "tiene el turno: negarlo detiene la cadena."
        ),
        method="POST",
        path="/api/v1/workflows/{radicado_id}/vistos-buenos/decidir",
        path_params=("radicado_id",),
        # El endpoint NO tiene gate de permiso: la autorización sale de la
        # IDENTIDAD —ser el revisor de turno—, que ningún permiso sustituye. Aun
        # así el catálogo declara `PERM_VOBO`, y por una razón concreta: una
        # ESCRITURA sin permiso declarado es visible para cualquiera, y eso
        # ofrece a todo el mundo un acto que solo puede ejecutar el revisor de
        # turno. Entre exigir de más —esconderla a un revisor sin `PERM_VOBO`,
        # caso que el reparto de permisos de este sistema no produce— y
        # ofrecérsela a toda la entidad, se elige lo primero y se DICE aquí.
        # `permiso_en_runtime` es la marca que declara justo eso al guardián
        # compartido: aquí no hay decorador que comparar porque la autorización
        # la resuelve el servicio con la identidad de quien llama.
        permission="PERM_VOBO",
        permiso_en_runtime=True,
        min_crud=1,
        write=True,
        destructive=False,
        epic="E05",
        operacion="Dar o negar el visto bueno",
        params=(
            _P_RADICADO_ID,
            Param(
                name="aprobar",
                type="boolean",
                required=True,
                description="true da el visto bueno, false lo niega y detiene la cadena.",
                label="Aprobar",
            ),
            Param(name="comentario", description="Motivo, sobre todo si se niega.", label="Comentario"),
        ),
    ),
    Tool(
        name="eventos_radicado",
        description=(
            "Historial APPEND-ONLY de eventos de flujo de un radicado: cada movimiento, "
            "quién lo hizo y cuándo. Es la traza probatoria, no un registro editable."
        ),
        method="GET",
        path="/api/v1/workflows/{radicado_id}/events",
        path_params=("radicado_id",),
        min_crud=1,
        write=False,
        epic="E05",
        operacion="Consultar los eventos de flujo de un radicado",
        params=(_P_RADICADO_ID,),
    ),
    Tool(
        name="revertir_asignacion",
        description=(
            "Revierte la ÚLTIMA asignación de un radicado, con motivo. Corrige un reparto "
            "equivocado; no borra el historial — la reversión también queda anotada."
        ),
        method="POST",
        path="/api/v1/workflows/{radicado_id}/rollback",
        path_params=("radicado_id",),
        permission="PERM_TRAMITAR",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E05",
        operacion="Revertir la última asignación",
        params=(
            _P_RADICADO_ID,
            Param(
                name="motivo",
                required=True,
                min_length=1,
                max_length=500,
                description="Por qué se revierte.",
                label="Motivo",
            ),
        ),
    ),
    # ── F3 Respuesta y despacho ─────────────────────────────────────────
    Tool(
        name="bandeja_envios",
        description=(
            "Envíos postales del tenant, con su estado. Es la bandeja de despacho: qué "
            "salió, por qué operador y en qué va."
        ),
        method="GET",
        path="/api/v1/envios",
        permission="PERM_RADI_SALIDA",
        min_crud=1,
        write=False,
        epic="E20",
        operacion="Consultar la bandeja de envíos",
        params=(
            Param(
                name="estado",
                enum=("registrado", "en_transito", "entregado", "devuelto", "fallido"),
                description="Filtra por estado del envío.",
                label="Estado",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="despachar_envio",
        description=(
            "Registra el despacho postal de un radicado por un operador. El operador es "
            "un vocabulario cerrado, no texto libre."
        ),
        method="POST",
        path="/api/v1/documents/{document_id}/envios",
        path_params=("document_id",),
        permission="PERM_RADI_SALIDA",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E20",
        operacion="Despachar un radicado por correo postal",
        params=(
            _P_DOCUMENT_ID,
            Param(
                name="operador",
                required=True,
                enum=("4-72", "servientrega", "otro"),
                description="Operador postal.",
                label="Operador",
            ),
            Param(name="destinatario", max_length=255, description="A quién se envía.", label="Destinatario"),
            Param(name="direccion", max_length=500, description="Dirección de entrega.", label="Dirección"),
        ),
    ),
    Tool(
        name="actualizar_estado_envio",
        description=(
            "Actualiza a mano el estado de entrega de un envío (despacho humano). Una "
            "notificación que implicaría retroceder, o sobre un estado terminal, se "
            "registra sin mutar el estado."
        ),
        method="POST",
        path="/api/v1/envios/{shipment_id}/estado",
        path_params=("shipment_id",),
        permission="PERM_RADI_SALIDA",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E20",
        operacion="Actualizar el estado de un envío",
        params=(
            Param(
                name="shipment_id",
                required=True,
                location="path",
                format="uuid",
                description="UUID del envío.",
                label="Envío",
            ),
            Param(
                name="estado",
                required=True,
                enum=("registrado", "en_transito", "entregado", "devuelto", "fallido"),
                description="Nuevo estado de entrega.",
                label="Estado",
            ),
            Param(name="descripcion", max_length=500, description="Detalle del evento.", label="Descripción"),
            Param(name="motivo", max_length=200, description="Motivo, si fue devuelto o falló.", label="Motivo"),
        ),
    ),
    Tool(
        name="consultar_envios_radicado",
        description="Envíos postales asociados a un radicado.",
        method="GET",
        path="/api/v1/documents/{document_id}/envios",
        path_params=("document_id",),
        permission="PERM_RADI_SALIDA",
        min_crud=1,
        write=False,
        epic="E20",
        operacion="Consultar los envíos de un radicado",
        params=(_P_DOCUMENT_ID,),
    ),
    Tool(
        name="consultar_borrador",
        description="Un borrador por su identificador, con su estado de aprobación.",
        method="GET",
        path="/api/v1/borradores/{borrador_id}",
        path_params=("borrador_id",),
        # `PERM_RADI` porque es lo que exige el endpoint: declararlo de menos
        # habría ofrecido la tool a quien recibe un 403 al usarla.
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E01",
        operacion="Consultar un borrador",
        params=(_P_BORRADOR_ID,),
    ),
    # ── F5 Archivo físico ───────────────────────────────────────────────
    #
    # El flujo que el asistente NO podía llevar: se veían los préstamos y nada
    # más. Un archivista no puede trabajar si no puede ubicar una caja.
    Tool(
        name="listar_ubicaciones",
        description=(
            "Ubicaciones del depósito, del nivel que se pida. La jerarquía va de sede a "
            "gaveta: una ubicación cuelga de otra, y por eso se filtra por nivel o por padre."
        ),
        method="GET",
        path="/api/v1/ubicaciones",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E17",
        operacion="Listar las ubicaciones del archivo físico",
        params=(
            Param(
                name="nivel",
                enum=("sede", "edificio", "piso", "deposito", "modulo", "estante", "entrepano", "gaveta", "custodia_externa"),
                description="Nivel de la jerarquía física.",
                label="Nivel",
            ),
            Param(name="parent_id", format="uuid", description="Ubicación padre.", label="Dentro de"),
            Param(
                name="tipo_archivo",
                enum=("gestion", "central", "historico"),
                description="Fase del archivo a la que pertenece.",
                label="Tipo de archivo",
            ),
        ),
    ),
    Tool(
        name="crear_ubicacion",
        description=(
            "Crea una ubicación física (sede, depósito, estante, entrepaño…). Cuelga de "
            "otra por `parent_id`: sin padre queda suelta, y una ubicación suelta no "
            "dice dónde está nada."
        ),
        method="POST",
        path="/api/v1/ubicaciones",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E17",
        operacion="Crear una ubicación en el archivo físico",
        params=(
            Param(
                name="nivel",
                required=True,
                enum=("sede", "edificio", "piso", "deposito", "modulo", "estante", "entrepano", "gaveta", "custodia_externa"),
                description="Nivel de la jerarquía física.",
                label="Nivel",
            ),
            Param(name="codigo", required=True, max_length=50, description="Código único de la ubicación.", label="Código"),
            Param(name="nombre", required=True, max_length=255, description="Nombre legible.", label="Nombre"),
            Param(name="parent_id", format="uuid", description="Ubicación que la contiene.", label="Dentro de"),
            Param(
                name="tipo_archivo",
                enum=("gestion", "central", "historico"),
                description="Fase del archivo.",
                label="Tipo de archivo",
            ),
            Param(name="capacidad_ml", type="number", description="Capacidad en metros lineales.", label="Capacidad (ml)"),
        ),
    ),
    Tool(
        name="consultar_ubicacion",
        description="Una ubicación por su identificador, con su posición en la jerarquía.",
        method="GET",
        path="/api/v1/ubicaciones/{ubicacion_id}",
        path_params=("ubicacion_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E17",
        operacion="Consultar una ubicación",
        params=(
            Param(
                name="ubicacion_id",
                required=True,
                location="path",
                format="uuid",
                description="UUID de la ubicación.",
                label="Ubicación",
            ),
        ),
    ),
    Tool(
        name="listar_unidades",
        description=(
            "Unidades de conservación (cajas, carpetas, legajos, tomos) con su ubicación "
            "y su estado."
        ),
        method="GET",
        path="/api/v1/unidades",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E17",
        operacion="Listar las unidades de conservación",
        params=(
            Param(
                name="tipo",
                enum=("caja", "carpeta", "legajo", "libro", "tomo", "az", "folder"),
                description="Tipo de unidad.",
                label="Tipo",
            ),
            Param(name="ubicacion_id", format="uuid", description="Ubicación donde está.", label="Ubicación"),
        ),
    ),
    Tool(
        name="crear_unidad",
        description=(
            "Crea una unidad de conservación y la coloca en una ubicación. Es el "
            "contenedor físico: lo que se presta y lo que se transfiere son unidades, "
            "no expedientes sueltos."
        ),
        method="POST",
        path="/api/v1/unidades",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E17",
        operacion="Crear una unidad de conservación",
        params=(
            Param(
                name="tipo",
                required=True,
                enum=("caja", "carpeta", "legajo", "libro", "tomo", "az", "folder"),
                description="Tipo de unidad.",
                label="Tipo",
            ),
            Param(name="codigo", required=True, max_length=50, description="Código único.", label="Código"),
            Param(name="ubicacion_id", format="uuid", description="Dónde se coloca.", label="Ubicación"),
            Param(name="parent_id", format="uuid", description="Unidad que la contiene.", label="Dentro de"),
            Param(name="soporte", max_length=50, description="Soporte del material (papel, digital…).", label="Soporte"),
            Param(name="num_folios", type="integer", minimum=0, description="Folios que contiene.", label="Folios"),
        ),
    ),
    Tool(
        name="contenido_unidad",
        description="Qué expedientes contiene una unidad, con su rango de folios.",
        method="GET",
        path="/api/v1/unidades/{unidad_id}/expedientes",
        path_params=("unidad_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E17",
        operacion="Consultar el contenido de una unidad",
        params=(_P_UNIDAD_ID,),
    ),
    Tool(
        name="ubicar_expediente_en_unidad",
        description=(
            "Coloca un expediente dentro de una unidad de conservación, con su rango de "
            "folios. Es lo que permite responder «¿dónde está físicamente este "
            "expediente?», que en un archivo es la pregunta que más se hace."
        ),
        method="POST",
        path="/api/v1/unidades/{unidad_id}/expedientes",
        path_params=("unidad_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E17",
        operacion="Ubicar un expediente en una unidad",
        params=(
            _P_UNIDAD_ID,
            Param(
                name="expediente_id",
                required=True,
                format="uuid",
                description="Expediente que se coloca.",
                label="Expediente",
            ),
            Param(name="folio_inicio", type="integer", minimum=0, description="Primer folio en la unidad.", label="Folio inicial"),
            Param(name="folio_fin", type="integer", minimum=0, description="Último folio en la unidad.", label="Folio final"),
        ),
    ),
    Tool(
        name="movimientos_unidad",
        description=(
            "Historial APPEND-ONLY de movimientos físicos de una unidad: cada traslado, "
            "préstamo y devolución. Es la cadena de custodia del soporte."
        ),
        method="GET",
        path="/api/v1/unidades/{unidad_id}/movimientos",
        path_params=("unidad_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E17",
        operacion="Consultar los movimientos de una unidad",
        params=(_P_UNIDAD_ID,),
    ),
    Tool(
        name="registrar_prestamo",
        description=(
            "Presta una unidad del archivo a alguien, con fecha de devolución esperada. "
            "Una unidad prestada bloquea su transferencia: el archivo no puede entregar "
            "lo que no tiene."
        ),
        method="POST",
        path="/api/v1/unidades/{unidad_id}/prestamos",
        path_params=("unidad_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E17",
        operacion="Registrar el préstamo de una unidad",
        params=(
            _P_UNIDAD_ID,
            Param(name="prestado_a", required=True, max_length=255, description="Persona o dependencia que la recibe.", label="Prestado a"),
            Param(name="prestado_por", max_length=255, description="Quién la entrega.", label="Entregado por"),
            Param(
                name="fecha_devolucion_esperada",
                required=True,
                format="date",
                description="Fecha en que debe volver (AAAA-MM-DD).",
                label="Devolución esperada",
            ),
        ),
    ),
    Tool(
        name="devolver_prestamo",
        description="Registra la devolución de una unidad prestada: vuelve a estar disponible.",
        method="POST",
        path="/api/v1/prestamos/{prestamo_id}/devolver",
        path_params=("prestamo_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E17",
        operacion="Registrar la devolución de un préstamo",
        params=(
            Param(
                name="prestamo_id",
                required=True,
                location="path",
                format="uuid",
                description="UUID del préstamo.",
                label="Préstamo",
            ),
        ),
    ),
    # ── F6 Transferencias ───────────────────────────────────────────────
    #
    # El ciclo de custodia entre fases del archivo. Cada acto cambia quién
    # responde del acervo, y por eso los tres irreversibles lo declaran.
    Tool(
        name="listar_transferencias",
        description="Transferencias del tenant, con su estado en el ciclo de custodia.",
        method="GET",
        path="/api/v1/transferencias",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E12",
        operacion="Listar las transferencias",
        params=(
            Param(
                name="estado",
                enum=("borrador", "enviada", "recibida", "rechazada"),
                description="Filtra por estado.",
                label="Estado",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="crear_transferencia",
        description=(
            "Abre una transferencia de un expediente entre fases del archivo: primaria "
            "(gestión → central) o secundaria (central → histórico). Nace en borrador; "
            "enviarla es un acto aparte."
        ),
        method="POST",
        path="/api/v1/transferencias",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E12",
        operacion="Crear una transferencia",
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
                description="Expediente que se transfiere. Debe estar cerrado.",
                label="Expediente",
            ),
            Param(name="ubicacion_origen_id", format="uuid", description="Ubicación de la que sale.", label="Origen"),
            Param(name="ubicacion_destino_id", format="uuid", description="Ubicación a la que va.", label="Destino"),
            Param(name="observacion", max_length=2000, description="Observaciones de la entrega.", label="Observación"),
        ),
    ),
    Tool(
        name="consultar_transferencia",
        description="Una transferencia por su identificador: estado, expediente y ubicaciones.",
        method="GET",
        path="/api/v1/transferencias/{transferencia_id}",
        path_params=("transferencia_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E12",
        operacion="Consultar una transferencia",
        params=(_P_TRANSFERENCIA_ID,),
    ),
    Tool(
        name="enviar_transferencia",
        description=(
            "Remite la transferencia al archivo receptor. A partir de aquí el expediente "
            "está EN TRÁNSITO: deja de poder modificarse y espera a que el receptor lo "
            "reciba o lo rechace."
        ),
        method="POST",
        path="/api/v1/transferencias/{transferencia_id}/enviar",
        path_params=("transferencia_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        # Cambia la custodia del acervo: quien responde del expediente pasa a ser
        # otro. No se «des-envía».
        destructive=True,
        epic="E12",
        operacion="Enviar una transferencia al receptor",
        params=(_P_TRANSFERENCIA_ID,),
    ),
    Tool(
        name="recibir_transferencia",
        description=(
            "El archivo receptor acepta la transferencia: verifica la fixity del acervo y "
            "CONGELA el acta de entrega. Es el acto que traslada la custodia — a partir "
            "de aquí responde el receptor."
        ),
        method="POST",
        path="/api/v1/transferencias/{transferencia_id}/recibir",
        path_params=("transferencia_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        destructive=True,
        epic="E12",
        operacion="Recibir una transferencia",
        params=(_P_TRANSFERENCIA_ID,),
    ),
    Tool(
        name="rechazar_transferencia",
        description=(
            "El receptor rechaza la transferencia con motivo. El expediente vuelve a la "
            "custodia del remitente; el motivo queda en la traza."
        ),
        method="POST",
        path="/api/v1/transferencias/{transferencia_id}/rechazar",
        path_params=("transferencia_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        destructive=True,
        epic="E12",
        operacion="Rechazar una transferencia",
        params=(
            _P_TRANSFERENCIA_ID,
            Param(
                name="motivo",
                required=True,
                min_length=1,
                description="Por qué se rechaza. Queda en el acta y en la auditoría.",
                label="Motivo",
            ),
        ),
    ),
    Tool(
        name="consultar_acta_transferencia",
        description=(
            "Acta de entrega, CONGELADA al recibir (Ac. AGN 001/2024). Dice qué se "
            "entregó, quién y cuándo — y no cambia después."
        ),
        method="GET",
        path="/api/v1/transferencias/{transferencia_id}/acta",
        path_params=("transferencia_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E12",
        operacion="Consultar el acta de una transferencia",
        params=(_P_TRANSFERENCIA_ID,),
    ),
    Tool(
        name="verificar_acta_transferencia",
        description=(
            "Verificación agregada del acta: el sello institucional y las firmas "
            "personales de cada parte. Dice qué está firmado y qué falta."
        ),
        method="GET",
        path="/api/v1/transferencias/{transferencia_id}/acta/verificacion",
        path_params=("transferencia_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E12",
        operacion="Verificar las firmas del acta",
        params=(_P_TRANSFERENCIA_ID,),
    ),
    # ── F4 Expediente (lo que faltaba del flujo) ────────────────────────
    Tool(
        name="listar_expedientes",
        description="Expedientes del tenant, filtrables por estado. Para buscar por texto, `buscar_expedientes`.",
        method="GET",
        path="/api/v1/expedientes",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E02",
        operacion="Listar expedientes",
        params=(
            Param(
                name="status",
                enum=("open", "closed", "transferred"),
                description="Estado del expediente.",
                label="Estado",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="excluir_radicado",
        description=(
            "Excluye un radicado del expediente con causal. NO lo borra: el índice "
            "conserva su lugar como hueco trazable con la causal a la vista — un "
            "expediente del que desaparecen documentos sin dejar rastro no sirve como "
            "prueba (RT-15 art. 4.3.2.3)."
        ),
        method="DELETE",
        path="/api/v1/expedientes/{expediente_id}/radicados/{radicado_id}",
        path_params=("expediente_id", "radicado_id"),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        destructive=True,
        epic="E02",
        operacion="Excluir un radicado de un expediente",
        params=(
            _P_EXPEDIENTE_ID,
            Param(
                name="radicado_id",
                required=True,
                location="path",
                format="uuid",
                description="Radicado que se excluye.",
                label="Radicado",
            ),
            Param(
                name="causal",
                required=True,
                description="Causal de la exclusión. Queda visible en el índice.",
                label="Causal",
            ),
        ),
    ),
    Tool(
        name="indice_xml",
        description=(
            "Índice electrónico del expediente en XML, el formato que exige el Acuerdo "
            "AGN 001/2024. Es el artefacto que se firma y se conserva, no una vista."
        ),
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/indice.xml",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E15",
        operacion="Obtener el índice electrónico en XML",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="verificar_indice",
        description=(
            "Verifica la integridad del índice: contrasta la huella de cada documento "
            "con la actual y re-calcula la del propio índice. Un documento SIN huella no "
            "es un documento alterado —se cuenta aparte y se dice— y no tumba la "
            "verificación."
        ),
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/indice/verify",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E15",
        operacion="Verificar la integridad del índice electrónico",
        params=(
            _P_EXPEDIENTE_ID,
            Param(name="version", type="integer", minimum=1, description="Versión a verificar (por defecto, la vigente).", label="Versión"),
        ),
    ),
    Tool(
        name="versiones_indice",
        description=(
            "Versiones del índice electrónico. Es append-only: cada cambio del conjunto "
            "documental genera una versión nueva y las anteriores se conservan."
        ),
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/indice/versions",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E15",
        operacion="Listar las versiones del índice electrónico",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="firmar_indice",
        description=(
            "Reintenta el sellado XAdES del índice electrónico. El cierre del expediente "
            "ya lo intenta; esto es para cuando quedó `pendiente_firma` porque el "
            "servicio de firma no respondió."
        ),
        method="POST",
        path="/api/v1/expedientes/{expediente_id}/indice/firmar",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E06",
        operacion="Firmar el índice electrónico",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="publicar_expediente",
        description=(
            "Publica el expediente en el portal de transparencia, o retira su "
            "publicación (Ley 1712/2014 art. 12). Exige fundamento."
        ),
        method="POST",
        path="/api/v1/expedientes/{expediente_id}/publicacion",
        path_params=("expediente_id",),
        permission="PERM_RECLASIFICAR",
        min_crud=3,
        write=True,
        destructive=True,
        epic="E13",
        operacion="Publicar de oficio un expediente",
        params=(
            _P_EXPEDIENTE_ID,
            Param(name="publicar", type="boolean", required=True, description="true publica, false retira.", label="Publicar"),
            Param(
                name="fundamento",
                required=True,
                min_length=1,
                max_length=500,
                description="Fundamento de la decisión.",
                label="Fundamento",
            ),
        ),
    ),
    Tool(
        name="historial_acceso_expediente",
        description=(
            "Quién ha LEÍDO o descargado los documentos del expediente. Es la traza de "
            "acceso, no la hoja de ruta: responde «¿quién vio esto?», que es la pregunta "
            "de una investigación."
        ),
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/historial-acceso",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E15",
        operacion="Consultar el historial de acceso al expediente",
        params=(_P_EXPEDIENTE_ID, _P_PAGE, _p_size(100)),
    ),
    Tool(
        name="roles_autorizados_expediente",
        description="Qué grupos pueden leer el expediente ahora mismo (instantánea viva).",
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/roles-autorizados",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E15",
        operacion="Consultar los grupos autorizados a leer el expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    # ── F7 Disposición y eliminación ────────────────────────────────────
    #
    # El único procedimiento del sistema que termina destruyendo documentos de
    # archivo, en cinco fases: proponer → publicar → avalar → acta firmada →
    # ejecutar. **La quinta NO se ofrece por este canal** (ver `NO_OFRECIDAS` en
    # `app/services/scope.py`): es la que borra, y su garantía no puede depender
    # de que un cliente ajeno haya preguntado a alguien.
    #
    # La fricción va repartida al revés de como suele hacerse: el acto que
    # PROTEGE —retirar el lote del procedimiento— es el más barato de todos, y
    # el que habilita la destrucción es el más caro. Si parar costara lo mismo
    # que seguir, la fricción empujaría hacia adelante.
    Tool(
        name="listar_lotes_eliminacion",
        description=(
            "Lotes de eliminación y en qué fase está cada uno: propuesto, publicado, "
            "avalado, ejecutado o anulado."
        ),
        method="GET",
        path="/api/v1/eliminacion/lotes",
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Listar los lotes de eliminación",
        params=(
            Param(
                name="estado",
                enum=("propuesto", "publicado", "avalado", "ejecutado", "anulado"),
                description="Fase del procedimiento.",
                label="Estado",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="consultar_lote_eliminacion",
        description=(
            "Un lote con su INVENTARIO CONGELADO: qué expedientes lo componen, con qué "
            "fundamento y en qué fase va. El inventario se congela al proponerlo y es la "
            "única fuente de qué se destruye — que no cambie entre la publicidad y la "
            "ejecución es la garantía de que se destruyó lo que se anunció."
        ),
        method="GET",
        path="/api/v1/eliminacion/lotes/{lote_id}",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar un lote de eliminación",
        params=(_P_LOTE_ID,),
    ),
    Tool(
        name="proponer_lote_eliminacion",
        description=(
            "Propone un lote de eliminación (fase 1 de 5) y CONGELA su inventario. No "
            "destruye nada y todavía se puede retirar: lo que hace es fijar qué "
            "expedientes entran, para que lo que se publique y lo que se destruya sean "
            "lo mismo. Cada expediente debe cumplir las condiciones de admisión "
            "(retención cumplida, disposición de eliminación, sin préstamo ni candado)."
        ),
        method="POST",
        path="/api/v1/eliminacion/lotes",
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        # Proponer no destruye y `anular_lote_eliminacion` lo retira. La fricción
        # se guarda para el acto que habilita la destrucción, no para el que la
        # prepara: gastarla aquí la devalúa donde de verdad hace falta.
        destructive=False,
        epic="E04",
        operacion="Proponer un lote de eliminación",
        params=(
            Param(
                name="codigo",
                required=True,
                max_length=60,
                description="Código del lote. Es como se le nombra en el acta y en la publicidad.",
                label="Código del lote",
            ),
            Param(
                name="expediente_ids",
                type="array",
                item_type="string",
                required=True,
                description="Expedientes que se proponen para eliminación.",
                label="Expedientes",
            ),
            Param(name="observaciones", description="Observaciones de la propuesta.", label="Observaciones"),
        ),
    ),
    Tool(
        name="publicar_inventario_lote",
        description=(
            "Publica el inventario del lote para que cualquiera pueda oponerse (fase 2 "
            "de 5, Ac. AGN 004/2019 art. 15). Abre un plazo con fecha de cierre: hasta "
            "que venza, el lote no se puede avalar. La publicidad previa es lo que "
            "convierte una eliminación en un acto legítimo y no en una pérdida."
        ),
        method="POST",
        path="/api/v1/eliminacion/lotes/{lote_id}/publicar",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        # Lo publicado se vio: retirarlo no lo deshace.
        destructive=True,
        epic="E04",
        operacion="Publicar el inventario de un lote de eliminación",
        params=(
            _P_LOTE_ID,
            Param(
                name="publicado_hasta",
                required=True,
                format="date",
                description="Fecha de cierre del plazo para oponerse (AAAA-MM-DD).",
                label="Publicado hasta",
            ),
        ),
    ),
    Tool(
        name="avalar_lote_eliminacion",
        description=(
            "Registra el aval del Comité de Gestión Documental (fase 3 de 5). **Este es "
            "el acto que habilita la destrucción**: a partir de aquí solo faltan el acta "
            "y la ejecución. No decide la eliminación —eso lo hizo el Comité, fuera del "
            "sistema— sino que deja constancia de su acto administrativo, y por eso pide "
            "el número de acta, la fecha y la referencia del acto."
        ),
        method="POST",
        path="/api/v1/eliminacion/lotes/{lote_id}/avalar",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=True,
        # El acto más caro del catálogo, y a propósito: para confirmarlo hay que
        # reproducir el identificador del lote, que solo se conoce mirando la
        # tarjeta. Una frase fija se teclea de memoria antes de leer nada.
        confirmar_con="lote_id",
        epic="E04",
        operacion="Registrar el aval del Comité sobre un lote de eliminación",
        params=(
            _P_LOTE_ID,
            Param(
                name="acta_comite",
                required=True,
                max_length=200,
                description="Número o referencia del acta del Comité que avaló el lote.",
                label="Acta del Comité",
            ),
            Param(
                name="fecha_aval",
                required=True,
                format="date",
                description="Fecha del aval (AAAA-MM-DD).",
                label="Fecha del aval",
            ),
            Param(
                name="acto_administrativo",
                required=True,
                max_length=200,
                description="Acto administrativo que respalda la eliminación.",
                label="Acto administrativo",
            ),
        ),
    ),
    Tool(
        name="anular_lote_eliminacion",
        description=(
            "Retira el lote del procedimiento con un motivo: los expedientes vuelven a "
            "estar como estaban y ya no se eliminarán. Es el acto que PARA la "
            "destrucción, y por eso es el más barato de confirmar de todo el "
            "procedimiento."
        ),
        method="POST",
        path="/api/v1/eliminacion/lotes/{lote_id}/anular",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        # Detener una eliminación no puede costar lo mismo que autorizarla:
        # fricción simétrica sobre actos opuestos empuja al que destruye.
        destructive=False,
        epic="E04",
        operacion="Retirar un lote del procedimiento de eliminación",
        params=(
            _P_LOTE_ID,
            Param(
                name="motivo",
                required=True,
                min_length=1,
                max_length=500,
                description="Por qué se retira. Queda en la traza del lote.",
                label="Motivo",
            ),
        ),
    ),
    Tool(
        name="generar_acta_eliminacion",
        description=(
            "Genera y sella el acta de eliminación (fase 4 de 5). El acta es de "
            "CONSERVACIÓN PERMANENTE: cuando los documentos ya no estén, es lo único "
            "que quedará para responder qué se destruyó, con qué fundamento y quién lo "
            "autorizó. Pide el método de borrado porque el acta tiene que decir cómo se "
            "destruyó, no solo que se destruyó."
        ),
        method="POST",
        path="/api/v1/eliminacion/lotes/{lote_id}/acta",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=True,
        epic="E04",
        operacion="Generar el acta de eliminación de un lote",
        params=(
            _P_LOTE_ID,
            Param(
                name="metodo_borrado",
                required=True,
                min_length=1,
                max_length=1000,
                description="Cómo se destruirá (borrado seguro, triturado, desmagnetizado…).",
                label="Método de borrado",
            ),
            Param(name="observaciones", description="Observaciones del acta.", label="Observaciones"),
        ),
    ),
    Tool(
        name="consultar_acta_eliminacion",
        description="El acta de eliminación de un lote, con su estado de firma.",
        method="GET",
        path="/api/v1/eliminacion/lotes/{lote_id}/acta",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar el acta de eliminación",
        params=(_P_LOTE_ID,),
    ),
    Tool(
        name="reintentar_firma_acta_eliminacion",
        description=(
            "Reintenta el sellado XAdES del acta. Existe porque la ejecución EXIGE el "
            "acta firmada: si el servicio de firma estaba caído, el procedimiento se "
            "detiene ahí a propósito — el último acto irreversible es exactamente el "
            "momento de no seguir con una constancia a medias."
        ),
        method="POST",
        path="/api/v1/eliminacion/lotes/{lote_id}/acta/firmar",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Reintentar la firma del acta de eliminación",
        params=(_P_LOTE_ID,),
    ),
    Tool(
        name="lapidas_lote",
        description=(
            "Lápidas de los expedientes ya eliminados: qué había, bajo qué acto se "
            "destruyó y cuándo. Es lo que impide que la respuesta a «¿qué pasó con este "
            "expediente?» sea el silencio, que es indistinguible de «nunca existió»."
        ),
        method="GET",
        path="/api/v1/eliminacion/lotes/{lote_id}/lapidas",
        path_params=("lote_id",),
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar las lápidas de un lote eliminado",
        params=(_P_LOTE_ID,),
    ),
    # ── F8 Preservación ─────────────────────────────────────────────────
    #
    # **Sellar en WORM NO está aquí, y no es un olvido.** El acto exige el
    # `file_id` del artefacto firmado, un identificador que solo conoce
    # archive-service en el contexto del expediente — por eso la propia interfaz
    # tampoco lo ofrece. Un modelo tendría que inventárselo, y sellar el fichero
    # equivocado bajo un candado de diez años no se deshace: la retención no se
    # acorta. La tarjeta de confirmación no protege de eso — enseñaría un UUID
    # que la persona no tiene forma de contrastar. El sellado ocurre solo, al
    # cerrar el expediente; lo que se ofrece aquí es VERLO y reintentar lo que
    # quedó a medias (ver `NO_OFRECIDAS` en `app/services/scope.py`).
    Tool(
        name="indices_pendientes_firma",
        description=(
            "Índices electrónicos cerrados que quedaron SIN sellar porque el servicio de "
            "firma no respondió. Es la cola de lo que hay que reintentar: un índice sin "
            "firma no acredita nada."
        ),
        method="GET",
        path="/api/v1/expedientes/indices/pendientes-firma",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E06",
        operacion="Consultar los índices pendientes de firma",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="artefactos_pendientes_renovacion",
        description=(
            "Artefactos protegidos cuya retención WORM está por vencer. Renovarla es un "
            "disparo de sistema; esta lista existe para VERLO venir, no para actuar."
        ),
        method="GET",
        path="/api/v1/expedientes/indices/pendientes-renovacion",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E10",
        operacion="Consultar los artefactos pendientes de renovación WORM",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="consultar_plan_preservacion",
        description=(
            "Plan de preservación digital VIGENTE de la entidad: formatos destino, "
            "formatos en riesgo de obsolescencia, número de copias y periodicidad de "
            "verificación de fixity."
        ),
        method="GET",
        path="/api/v1/preservacion/plan",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E10",
        operacion="Consultar el plan de preservación digital",
        params=(),
    ),
    Tool(
        name="versiones_plan_preservacion",
        description=(
            "Versiones del plan de preservación, con quién declaró cada una y cuándo. Es "
            "append-only: cambiar la política crea una versión nueva y la anterior se "
            "conserva."
        ),
        method="GET",
        path="/api/v1/preservacion/plan/versions",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E10",
        operacion="Consultar las versiones del plan de preservación",
        params=(),
    ),
    Tool(
        name="declarar_plan_preservacion",
        description=(
            "Declara una versión NUEVA del plan de preservación. No edita la vigente: la "
            "sustituye dejándola en el historial, así que un error se corrige declarando "
            "otra — por eso se aprueba sin teclear nada.\n\n"
            "`formatos_origen` es la política de obsolescencia de la entidad: los tipos "
            "MIME que el barrido periódico convertirá a PDF/A. Vacío significa **no "
            "migrar nada**; qué formato está en riesgo es un juicio archivístico del "
            "PINAR, no una lista que traiga el fabricante."
        ),
        method="PUT",
        path="/api/v1/preservacion/plan",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        # Versionado append-only: la corrección es declarar otra versión, y la
        # anterior sigue ahí. Gastar fricción aquí la devalúa donde sí hace falta.
        destructive=False,
        epic="E10",
        operacion="Declarar una versión del plan de preservación",
        params=(
            Param(
                name="formatos_destino",
                type="array",
                item_type="string",
                description="Formatos de conservación (por ejemplo PDF/A-2b).",
                label="Formatos destino",
            ),
            Param(
                name="formatos_origen",
                type="array",
                item_type="string",
                description="Tipos MIME en riesgo de obsolescencia que se migrarán.",
                label="Formatos en riesgo",
            ),
            Param(
                name="num_copias",
                type="integer",
                minimum=1,
                description="Copias de preservación que exige el plan.",
                label="Copias",
            ),
            Param(
                name="periodicidad_fixity_dias",
                type="integer",
                minimum=1,
                description="Cada cuántos días se verifica la fixity.",
                label="Periodicidad fixity",
            ),
            Param(name="politica_migracion", description="Política de migración declarada.", label="Política de migración"),
            Param(name="contingencia", description="Plan de contingencia.", label="Contingencia"),
        ),
    ),
    Tool(
        name="consultar_aip",
        description=(
            "Paquete de archivo (AIP, BagIt + PREMIS) de un expediente: qué se empaquetó "
            "para conservación a largo plazo y con qué huellas. El empaquetado lo dispara "
            "el sistema al cerrar; esto lo consulta."
        ),
        method="GET",
        # La ruta MEDIADA de archive-service, no la de storage. La de storage
        # (`/preservacion/aip/{id}`) está sellada con `X-Internal-Token` y solo la
        # alcanza el propio servicio: es el mediador quien resuelve el clearance
        # del llamante contra el nivel VIGENTE del expediente, que storage no
        # modela. Apuntar a la sellada habría dado 401 a todo el mundo — lo cazó
        # el guardián compartido al cruzar el permiso declarado con el real.
        path="/api/v1/expedientes/{expediente_id}/aip",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E10",
        operacion="Consultar el AIP de preservación de un expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="eventos_preservacion",
        description=(
            "Eventos PREMIS del expediente: ingesta, fijación, migración, validación "
            "PDF/A, sellado WORM, réplica y renovación. Es el registro inmutable de toda "
            "intervención de preservación sobre él."
        ),
        method="GET",
        # Igual que `consultar_aip`: la ruta mediada, no la sellada de storage.
        path="/api/v1/expedientes/{expediente_id}/preservacion/eventos",
        path_params=("expediente_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E10",
        operacion="Consultar los eventos de preservación de un expediente",
        params=(_P_EXPEDIENTE_ID,),
    ),
    Tool(
        name="registrar_evento_preservacion",
        description=(
            "Anota un evento de preservación en el registro PREMIS. **Es append-only: lo "
            "anotado no se corrige ni se retira**, así que una intervención mal descrita "
            "queda dicha para siempre. Se usa para dejar constancia de actos hechos "
            "fuera del sistema (una migración de soporte, una réplica en sede alterna)."
        ),
        method="POST",
        path="/api/v1/preservacion/eventos",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        # No destruye, pero se escribe en un registro que no admite corrección:
        # lo irreversible aquí es la afirmación, no la pérdida.
        destructive=True,
        epic="E10",
        operacion="Registrar un evento de preservación",
        params=(
            Param(
                name="object_ref",
                required=True,
                max_length=255,
                description="Objeto sobre el que se actuó.",
                label="Objeto",
            ),
            Param(
                name="tipo",
                required=True,
                enum=("INGESTA", "FIJACION", "MIGRACION", "VALIDACION_PDFA", "WORM", "REPLICA", "RENOVACION"),
                description="Tipo de intervención (PREMIS).",
                label="Tipo",
            ),
            Param(
                name="resultado",
                enum=("ok", "fallo", "no_evaluado"),
                description=(
                    "`no_evaluado` NO es un aprobado con reservas: dice que la "
                    "comprobación no se hizo. Úsalo cuando no hubo con qué verificar."
                ),
                label="Resultado",
            ),
            Param(name="hash_before", description="Huella antes de la intervención.", label="Huella antes"),
            Param(name="hash_after", description="Huella después.", label="Huella después"),
        ),
    ),
    Tool(
        name="migrar_formato",
        description=(
            "Convierte un archivo a PDF/A contra la obsolescencia (Decreto 2609/2012 "
            "art. 32). **El original se conserva intacto** y el derivado convive con él: "
            "migrar no destruye. Nada se promueve si no pasa el validador."
        ),
        method="POST",
        path="/api/v1/preservacion/migrar-formato",
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E10",
        operacion="Migrar un archivo a PDF/A",
        params=(
            Param(
                name="file_id",
                required=True,
                format="uuid",
                description="Archivo a convertir.",
                label="Archivo",
            ),
            Param(
                name="perfil",
                enum=("1b", "2b", "3b"),
                description="Perfil PDF/A de destino.",
                label="Perfil",
            ),
        ),
    ),
    Tool(
        name="cadena_migraciones",
        description=(
            "Procedencia de un archivo: de qué se migró, con qué herramienta y con qué "
            "resultado. Responde «¿este PDF/A de dónde salió?», que es la pregunta que "
            "hace válida una copia de conservación."
        ),
        method="GET",
        path="/api/v1/preservacion/migraciones/{file_id}",
        path_params=("file_id",),
        permission="USUA_PERM_EXPEDIENTE",
        min_crud=1,
        write=False,
        epic="E10",
        operacion="Consultar la cadena de migraciones de un archivo",
        params=(
            Param(
                name="file_id",
                required=True,
                location="path",
                format="uuid",
                description="Archivo del que se pide la procedencia.",
                label="Archivo",
            ),
        ),
    ),
    # ── F9 Instrumentos archivísticos ───────────────────────────────────
    #
    # La TRD es lo que fija cuánto se conserva cada cosa y qué se hace con ella
    # al final: es el instrumento del que cuelga todo lo demás. Su circuito es
    # append-only y tiene cinco estados (borrador → aprobada → convalidada →
    # derogada, más la devolución con observaciones), y la fricción va donde el
    # error es irreparable: **en el número de versión**.
    #
    # Convalidar y derogar cambian QUÉ TABLA GOBIERNA la clasificación de aquí
    # en adelante. Equivocarse de versión —convalidar la v3 creyendo que era la
    # v4— no se deshace con otro acto: hay que crear una versión nueva y volver
    # a recorrer el circuito, con la tabla equivocada gobernando mientras tanto.
    # Por eso ambos piden que se teclee el número de versión, que solo se conoce
    # mirando la tarjeta.
    Tool(
        name="crear_serie_trd",
        description=(
            "Crea una serie documental de la TRD en estado BORRADOR. No gobierna nada "
            "todavía: para que rija hay que aprobarla y convalidarla."
        ),
        method="POST",
        path="/api/v1/trd",
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Crear una serie de la TRD",
        params=(
            Param(name="code", required=True, max_length=50, description="Código de la serie.", label="Código"),
            Param(name="name", required=True, max_length=255, description="Nombre de la serie.", label="Nombre"),
            Param(name="description", description="Alcance y contenido de la serie.", label="Descripción"),
            Param(
                name="retention_years",
                type="integer",
                minimum=0,
                description="Años en archivo de gestión.",
                label="Retención gestión",
            ),
            Param(
                name="total_retention",
                type="integer",
                minimum=0,
                description="Años totales (gestión + central).",
                label="Retención total",
            ),
            Param(
                name="disposition",
                enum=("CT", "E", "S", "M"),
                description=(
                    "Disposición final: CT conservación total, E eliminación, "
                    "S selección, M digitalización/microfilmación."
                ),
                label="Disposición final",
            ),
        ),
    ),
    Tool(
        name="versiones_serie_trd",
        description=(
            "Historial completo de versiones de una serie TRD: quién aprobó cada una, "
            "con qué acto administrativo se convalidó y cuál rige hoy. Es append-only: "
            "las versiones anteriores no se borran."
        ),
        method="GET",
        path="/api/v1/trd/{code}/versiones",
        path_params=("code",),
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar el historial de versiones de una serie TRD",
        params=(_P_TRD_CODE,),
    ),
    Tool(
        name="nueva_version_serie_trd",
        description=(
            "Abre una versión BORRADOR de una serie ya existente. Es la única forma de "
            "cambiar una TRD que ya rige: la vigente no se edita, se sustituye — así "
            "queda constancia de qué reglas se aplicaron a lo que se clasificó antes."
        ),
        method="POST",
        path="/api/v1/trd/{code}/versiones",
        path_params=("code",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Crear una versión nueva de una serie TRD",
        params=(
            _P_TRD_CODE,
            Param(name="name", max_length=255, description="Nombre de la serie en esta versión.", label="Nombre"),
            Param(name="description", description="Alcance y contenido.", label="Descripción"),
            Param(name="retention_years", type="integer", minimum=0, description="Años en gestión.", label="Retención gestión"),
            Param(name="total_retention", type="integer", minimum=0, description="Años totales.", label="Retención total"),
            Param(
                name="disposition",
                enum=("CT", "E", "S", "M"),
                description="Disposición final de esta versión.",
                label="Disposición final",
            ),
        ),
    ),
    Tool(
        name="aprobar_version_trd",
        description=(
            "Aprueba una versión borrador y deja constancia del acta del Comité "
            "(borrador → aprobada). Todavía NO rige: falta convalidarla. Y tiene "
            "vuelta atrás — `devolver_version_trd` la regresa a borrador."
        ),
        method="POST",
        path="/api/v1/trd/{code}/versiones/{version}/aprobar",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        # El propio sistema ofrece el camino de vuelta (`devolver`): poner aquí
        # la fricción del acto irreversible sería gastarla donde hay remedio.
        destructive=False,
        epic="E04",
        operacion="Aprobar una versión de la TRD",
        params=(
            _P_TRD_CODE,
            _P_VERSION,
            Param(
                name="acta_comite",
                required=True,
                max_length=200,
                description="Acta del Comité que aprobó la versión.",
                label="Acta del Comité",
            ),
            Param(
                name="fecha_aprobacion_comite",
                required=True,
                format="date",
                description="Fecha del acta (AAAA-MM-DD).",
                label="Fecha de aprobación",
            ),
        ),
    ),
    Tool(
        name="devolver_version_trd",
        description=(
            "Devuelve una versión aprobada a borrador con observaciones. Es el camino de "
            "vuelta del circuito, y por eso se aprueba sin teclear nada: corregir tiene "
            "que costar menos que consumar."
        ),
        method="POST",
        path="/api/v1/trd/{code}/versiones/{version}/devolver",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Devolver una versión de la TRD con observaciones",
        params=(
            _P_TRD_CODE,
            _P_VERSION,
            Param(
                name="motivo",
                required=True,
                min_length=1,
                description="Observaciones que motivan la devolución.",
                label="Motivo",
            ),
        ),
    ),
    Tool(
        name="convalidar_version_trd",
        description=(
            "Convalida una versión aprobada: **pasa a ser la que rige** y DEROGA a la "
            "vigente en el mismo acto (aprobada → convalidada). Desde aquí, todo lo que "
            "se clasifique se clasifica con esta tabla. Pide el acto administrativo y la "
            "instancia convalidante porque una TRD rige por un acto, no por un clic."
        ),
        method="POST",
        path="/api/v1/trd/{code}/versiones/{version}/convalidar",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=True,
        # La fricción sobre el dato cuyo error es irreparable: convalidar la
        # versión equivocada no se corrige con otro acto —hay que abrir una
        # versión nueva y recorrer el circuito entero, con la tabla equivocada
        # gobernando mientras tanto—.
        confirmar_con="version",
        epic="E04",
        operacion="Convalidar una versión de la TRD",
        params=(
            _P_TRD_CODE,
            _P_VERSION,
            Param(
                name="acto_administrativo",
                required=True,
                max_length=200,
                description="Acto administrativo que la convalida.",
                label="Acto administrativo",
            ),
            Param(
                name="fecha_convalidacion",
                required=True,
                format="date",
                description="Fecha de la convalidación (AAAA-MM-DD).",
                label="Fecha",
            ),
            Param(
                name="instancia_convalidante",
                required=True,
                max_length=200,
                description="Instancia que convalida (Consejo Departamental, AGN…).",
                label="Instancia",
            ),
            Param(name="rusd_radicado", description="Radicado de inscripción en el RUSD.", label="RUSD"),
        ),
    ),
    Tool(
        name="derogar_version_trd",
        description=(
            "Deroga una versión convalidada. **Esa serie deja de servir para clasificar**, "
            "así que si hay expedientes abiertos que dependan de ella el sistema se "
            "niega y explica cómo proceder. No se deshace: para volver a tener una serie "
            "vigente hay que recorrer el circuito otra vez."
        ),
        method="POST",
        path="/api/v1/trd/{code}/versiones/{version}/derogar",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=True,
        confirmar_con="version",
        epic="E04",
        operacion="Derogar una versión de la TRD",
        params=(_P_TRD_CODE, _P_VERSION),
    ),
    Tool(
        name="registrar_rusd_trd",
        description=(
            "Anota la inscripción en el RUSD y/o la fecha de publicación de una versión "
            "ya convalidada. Es constancia registral: no cambia qué tabla rige."
        ),
        method="POST",
        path="/api/v1/trd/{code}/versiones/{version}/registrar-rusd",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Registrar la inscripción RUSD de una versión de la TRD",
        params=(
            _P_TRD_CODE,
            _P_VERSION,
            Param(name="rusd_radicado", description="Radicado de la inscripción.", label="RUSD"),
            Param(name="fecha_publicacion", format="date", description="Fecha de publicación.", label="Fecha de publicación"),
        ),
    ),
    Tool(
        name="disposicion_vencida",
        description=(
            "Expedientes cuyo calendario de retención ya venció y esperan decisión. El "
            "sistema los CUENTA y avisa; no ejecuta nada por su cuenta."
        ),
        method="GET",
        path="/api/v1/trd/disposicion-vencida",
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar los expedientes con disposición vencida",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="decidir_disposicion_vencida",
        description=(
            "Decide sobre un vencimiento: confirmar que la disposición procede, o "
            "aplazarla con motivo. **Confirmar no destruye nada** —la eliminación tiene "
            "su propio procedimiento de cinco fases— pero sí deja el expediente listo "
            "para entrar en él."
        ),
        method="POST",
        path="/api/v1/trd/disposicion-vencida/{expediente_id}/decidir",
        path_params=("expediente_id",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=True,
        epic="E04",
        operacion="Decidir sobre una disposición vencida",
        params=(
            _P_EXPEDIENTE_ID,
            Param(
                name="fase",
                required=True,
                enum=("gestion", "central"),
                description="Fase del archivo cuyo plazo venció.",
                label="Fase",
            ),
            Param(
                name="decision",
                required=True,
                enum=("confirmada", "aplazada"),
                description="Confirmar la disposición o aplazarla.",
                label="Decisión",
            ),
            Param(name="motivo", description="Motivo, obligatorio al aplazar.", label="Motivo"),
        ),
    ),
    Tool(
        name="revision_trd_pendiente",
        description=(
            "Expedientes marcados para revisar su clasificación porque la TRD que los "
            "gobernaba cambió. Es la consecuencia práctica de convalidar una versión "
            "nueva: alguien tiene que mirar qué queda descolocado."
        ),
        method="GET",
        path="/api/v1/trd/revision-pendiente",
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar los expedientes pendientes de revisión de TRD",
        params=(_P_PAGE, _p_size(100)),
    ),
    # ── F9 · TVD: el mismo circuito, para el fondo acumulado ────────────
    #
    # La TVD valora lo que se produjo SIN criterio archivístico —lo que dejó una
    # entidad liquidada, una dependencia suprimida—. Su circuito es gemelo del de
    # la TRD y la fricción va en el mismo sitio y por la misma razón.
    Tool(
        name="listar_tvd",
        description=(
            "Agrupaciones documentales de la TVD (Tabla de Valoración Documental): lo "
            "que gobierna el fondo acumulado."
        ),
        method="GET",
        path="/api/v1/tvd",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Listar las agrupaciones de la TVD",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="consultar_tvd",
        description="Una agrupación de la TVD por su identificador.",
        method="GET",
        path="/api/v1/tvd/{agrupacion_id}",
        path_params=("agrupacion_id",),
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar una agrupación de la TVD",
        params=(_P_AGRUPACION_ID,),
    ),
    Tool(
        name="retencion_tvd",
        description=(
            "Calendario de retención y disposición que le corresponde a una agrupación "
            "del fondo acumulado."
        ),
        method="GET",
        path="/api/v1/tvd/{agrupacion_id}/retention",
        path_params=("agrupacion_id",),
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar la retención de una agrupación TVD",
        params=(_P_AGRUPACION_ID,),
    ),
    Tool(
        name="crear_agrupacion_tvd",
        description="Crea una agrupación de la TVD en estado BORRADOR.",
        method="POST",
        path="/api/v1/tvd",
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Crear una agrupación de la TVD",
        params=(
            Param(name="code", required=True, max_length=50, description="Código de la agrupación.", label="Código"),
            Param(name="name", required=True, max_length=255, description="Nombre.", label="Nombre"),
            Param(name="description", description="Alcance de la agrupación.", label="Descripción"),
            Param(
                name="disposition",
                enum=("CT", "E", "S", "M"),
                description="Disposición final.",
                label="Disposición final",
            ),
        ),
    ),
    Tool(
        name="versiones_agrupacion_tvd",
        description="Historial de versiones de una agrupación TVD. Append-only, igual que la TRD.",
        method="GET",
        path="/api/v1/tvd/{code}/versiones",
        path_params=("code",),
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar el historial de versiones de una agrupación TVD",
        params=(_P_TVD_CODE,),
    ),
    Tool(
        name="nueva_version_agrupacion_tvd",
        description="Abre una versión borrador de una agrupación TVD ya existente.",
        method="POST",
        path="/api/v1/tvd/{code}/versiones",
        path_params=("code",),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Crear una versión nueva de una agrupación TVD",
        params=(
            _P_TVD_CODE,
            Param(name="name", max_length=255, description="Nombre en esta versión.", label="Nombre"),
            Param(name="description", description="Alcance.", label="Descripción"),
            Param(
                name="disposition",
                enum=("CT", "E", "S", "M"),
                description="Disposición final de esta versión.",
                label="Disposición final",
            ),
        ),
    ),
    Tool(
        name="aprobar_version_tvd",
        description=(
            "Aprueba una versión borrador de la TVD con el acta del Comité. Tiene vuelta "
            "atrás: `devolver_version_tvd`."
        ),
        method="POST",
        path="/api/v1/tvd/{code}/versiones/{version}/aprobar",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Aprobar una versión de la TVD",
        params=(
            _P_TVD_CODE,
            _P_VERSION,
            Param(name="acta_comite", required=True, max_length=200, description="Acta del Comité.", label="Acta del Comité"),
            Param(
                name="fecha_aprobacion_comite",
                required=True,
                format="date",
                description="Fecha del acta (AAAA-MM-DD).",
                label="Fecha de aprobación",
            ),
        ),
    ),
    Tool(
        name="devolver_version_tvd",
        description="Devuelve una versión aprobada de la TVD a borrador, con observaciones.",
        method="POST",
        path="/api/v1/tvd/{code}/versiones/{version}/devolver",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Devolver una versión de la TVD con observaciones",
        params=(
            _P_TVD_CODE,
            _P_VERSION,
            Param(name="motivo", required=True, min_length=1, description="Observaciones.", label="Motivo"),
        ),
    ),
    Tool(
        name="convalidar_version_tvd",
        description=(
            "Convalida una versión de la TVD: pasa a regir y deroga a la vigente en el "
            "mismo acto."
        ),
        method="POST",
        path="/api/v1/tvd/{code}/versiones/{version}/convalidar",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=True,
        confirmar_con="version",
        epic="E04",
        operacion="Convalidar una versión de la TVD",
        params=(
            _P_TVD_CODE,
            _P_VERSION,
            Param(name="acto_administrativo", required=True, max_length=200, description="Acto que la convalida.", label="Acto administrativo"),
            Param(name="fecha_convalidacion", required=True, format="date", description="Fecha (AAAA-MM-DD).", label="Fecha"),
            Param(name="instancia_convalidante", required=True, max_length=200, description="Instancia que convalida.", label="Instancia"),
            Param(name="rusd_radicado", description="Radicado RUSD.", label="RUSD"),
        ),
    ),
    Tool(
        name="derogar_version_tvd",
        description=(
            "Deroga una versión convalidada de la TVD: esa agrupación deja de servir "
            "para valorar el fondo acumulado."
        ),
        method="POST",
        path="/api/v1/tvd/{code}/versiones/{version}/derogar",
        path_params=("code", "version"),
        permission="USUA_PERM_TRD",
        min_crud=3,
        write=True,
        destructive=True,
        confirmar_con="version",
        epic="E04",
        operacion="Derogar una versión de la TVD",
        params=(_P_TVD_CODE, _P_VERSION),
    ),
    # ── F9 · Tipos documentales y metadatos ─────────────────────────────
    Tool(
        name="consultar_tipo_documental",
        description="Un tipo documental por su identificador, con la serie de la que cuelga.",
        method="GET",
        path="/api/v1/tipos-documentales/{tipo_id}",
        path_params=("tipo_id",),
        min_crud=1,
        write=False,
        epic="E04",
        operacion="Consultar un tipo documental",
        params=(_P_TIPO_ID,),
    ),
    Tool(
        name="crear_tipo_documental",
        description=(
            "Crea un tipo documental colgando de una serie de la TRD. Es el vocabulario "
            "con el que se clasifica al radicar: lo que no está aquí, no se puede elegir."
        ),
        method="POST",
        path="/api/v1/tipos-documentales",
        permission="USUA_PERM_TRD",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E04",
        operacion="Crear un tipo documental",
        params=(
            Param(name="code", required=True, max_length=50, description="Código del tipo.", label="Código"),
            Param(name="nombre", required=True, max_length=255, description="Nombre del tipo.", label="Nombre"),
            Param(name="trd_serie_id", format="uuid", description="Serie de la TRD a la que pertenece.", label="Serie"),
            Param(
                name="obligatorio",
                type="boolean",
                description="Si el tipo es obligatorio en su serie.",
                label="Obligatorio",
            ),
        ),
    ),
    Tool(
        name="listar_elementos_metadato",
        description=(
            "Elementos de metadato declarados por la entidad: el vocabulario con el que "
            "se describen los documentos más allá de los campos fijos."
        ),
        method="GET",
        path="/api/v1/metadata/elements",
        min_crud=1,
        write=False,
        epic="E03",
        operacion="Listar los elementos de metadato",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="listar_plantillas_metadato",
        description="Plantillas de metadatos, opcionalmente las de un tipo documental.",
        method="GET",
        path="/api/v1/metadata/templates",
        min_crud=1,
        write=False,
        epic="E03",
        operacion="Listar las plantillas de metadatos",
        params=(
            Param(name="tipo_documental", description="Filtra por tipo documental.", label="Tipo documental"),
        ),
    ),
    # ── F9 · PINAR (Plan Institucional de Archivos) ─────────────────────
    #
    # Se ofrece la LECTURA y el registro de avance; la estructura del plan
    # —crear objetivos, proyectos, aspectos, priorizar— NO. No es una cuestión de
    # riesgo: es que armar un PINAR es un ejercicio de planeación con el Comité
    # delante, no una secuencia de altas. Lo que un asistente aporta aquí es
    # decir cómo va, no decidir qué se hace (ver `NO_OFRECIDAS` en
    # `app/services/scope.py`).
    Tool(
        name="listar_planes_pinar",
        description=(
            "Planes Institucionales de Archivos de la entidad (Ac. AGN 003/2015), con su "
            "estado: formulación, aprobado, en ejecución o cerrado."
        ),
        method="GET",
        path="/api/v1/pinar/planes",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Listar los planes PINAR",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="consultar_plan_pinar",
        description="Un plan PINAR con su vigencia, su estado y su acto de aprobación.",
        method="GET",
        path="/api/v1/pinar/planes/{plan_id}",
        path_params=("plan_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Consultar un plan PINAR",
        params=(_P_PLAN_ID,),
    ),
    Tool(
        name="tablero_pinar",
        description=(
            "Tablero de un plan PINAR: cuánto ha avanzado cada proyecto y cómo va el "
            "plan en conjunto. Es la respuesta a «¿cómo vamos?» sin tener que recorrer "
            "proyecto por proyecto."
        ),
        method="GET",
        path="/api/v1/pinar/planes/{plan_id}/tablero",
        path_params=("plan_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Consultar el tablero de un plan PINAR",
        params=(_P_PLAN_ID,),
    ),
    Tool(
        name="mapa_ruta_pinar",
        description="Mapa de ruta del plan: qué proyecto va en qué tiempo.",
        method="GET",
        path="/api/v1/pinar/planes/{plan_id}/mapa-ruta",
        path_params=("plan_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Consultar el mapa de ruta de un plan PINAR",
        params=(_P_PLAN_ID,),
    ),
    Tool(
        name="objetivos_pinar",
        description="Objetivos de un plan PINAR.",
        method="GET",
        path="/api/v1/pinar/planes/{plan_id}/objetivos",
        path_params=("plan_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Consultar los objetivos de un plan PINAR",
        params=(_P_PLAN_ID,),
    ),
    Tool(
        name="proyectos_pinar",
        description="Proyectos de un plan PINAR, con su avance.",
        method="GET",
        path="/api/v1/pinar/planes/{plan_id}/proyectos",
        path_params=("plan_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Consultar los proyectos de un plan PINAR",
        params=(_P_PLAN_ID,),
    ),
    Tool(
        name="priorizacion_pinar",
        description=(
            "Priorización del plan: qué aspectos críticos pesan más, que es lo que "
            "ordena los proyectos."
        ),
        method="GET",
        path="/api/v1/pinar/planes/{plan_id}/priorizacion",
        path_params=("plan_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Consultar la priorización de un plan PINAR",
        params=(_P_PLAN_ID,),
    ),
    Tool(
        name="seguimiento_proyecto_pinar",
        description="Historial de avance de un proyecto del PINAR.",
        method="GET",
        path="/api/v1/pinar/proyectos/{proyecto_id}/seguimiento",
        path_params=("proyecto_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E14",
        operacion="Consultar el seguimiento de un proyecto PINAR",
        params=(_P_PROYECTO_ID,),
    ),
    Tool(
        name="registrar_avance_pinar",
        description=(
            "Anota el avance de un proyecto del PINAR en una fecha. Es acumulativo: cada "
            "anotación se suma al historial y las anteriores se conservan, así que un "
            "porcentaje mal puesto se corrige anotando el correcto."
        ),
        method="POST",
        path="/api/v1/pinar/proyectos/{proyecto_id}/seguimiento",
        path_params=("proyecto_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E14",
        operacion="Registrar el avance de un proyecto PINAR",
        params=(
            _P_PROYECTO_ID,
            Param(
                name="fecha",
                required=True,
                format="date",
                description="Fecha del corte de avance (AAAA-MM-DD).",
                label="Fecha",
            ),
            Param(
                name="avance_pct",
                type="integer",
                required=True,
                minimum=0,
                maximum=100,
                description="Porcentaje de avance acumulado.",
                label="Avance (%)",
            ),
            Param(name="observacion", description="Qué se hizo en este periodo.", label="Observación"),
        ),
    ),
    Tool(
        name="reporte_radicacion",
        description=(
            "Resumen ESTADÍSTICO de la radicación en un periodo: cuántos radicados por "
            "tipo, estado, mes y dependencia. Son conteos, no un listado: para ver "
            "radicados concretos está 'buscar_radicados'."
        ),
        method="GET",
        path="/api/v1/reports/radicados",
        # `SGD_PERM_ESTADISTICA`, el permiso que la interfaz ya usaba para
        # mostrar el menú «Reportes» y que el endpoint no exigía. Al gatearlo,
        # `shared/tests/test_permisos_declarados.py` puso esta entrada en rojo
        # sola —declaraba «sin permiso» sobre un endpoint que ahora sí lo pide—,
        # que es exactamente para lo que existe ese guardián.
        #
        # Devuelve SOLO agregados: ni número de radicado, ni asunto, ni
        # remitente. La advertencia sigue en la descripción para que el modelo no
        # lo use como sustituto de una búsqueda y presente un conteo como si
        # fuera un inventario.
        permission="SGD_PERM_ESTADISTICA",
        min_crud=1,
        write=False,
        epic="E09",
        operacion="Ver el resumen estadístico de radicación",
        params=(
            Param(
                name="date_from",
                format="date",
                description="Radicados registrados desde esta fecha (AAAA-MM-DD).",
                label="Desde",
            ),
            Param(
                name="date_to",
                format="date",
                description="Radicados registrados hasta esta fecha (AAAA-MM-DD).",
                label="Hasta",
            ),
        ),
    ),
    # ── F12 · Administración y seguridad ────────────────────────────────
    #
    # Este flujo se resuelve casi entero DECLARANDO LO QUE NO SE OFRECE, y
    # conviene decir por qué antes de la primera tool, porque a primera vista
    # parece la sección más pobre del catálogo: 59 operaciones y ninguna
    # escritura.
    #
    # La razón no es el riesgo genérico de escribir. Es que aquí viven las tres
    # cosas de las que depende que todo lo demás signifique algo:
    #
    # 1. **Quién es quién y qué puede hacer** (usuarios, grupos, permisos,
    #    clearance, URD). Conceder un permiso desde un chat rompe el propio
    #    marco de alcance: el asistente pasaría de trabajar DENTRO de los
    #    límites del usuario a poder ensancharlos. Un sistema en el que la
    #    frontera de autorización es alcanzable por quien está dentro de ella
    #    no tiene frontera.
    # 2. **Las credenciales personales** (TOTP, llave de firma). El secreto y
    #    los códigos de recuperación se revelan EXACTAMENTE UNA VEZ, por
    #    diseño; entregarlos por este canal los dejaría escritos en el
    #    historial de una conversación. Se ofrece el ESTADO —¿tengo el segundo
    #    factor activo?— porque es justo lo que hace falta saber antes de
    #    intentar una firma, y no revela nada.
    # 3. **La regla con la que se cuentan los plazos** (festivos, parámetros) y
    #    **el vocabulario cerrado** (catálogos, dependencias). Un festivo que
    #    se añade hoy mueve el vencimiento de todos los términos que ya están
    #    corriendo, hacia atrás y sin aviso; y un catálogo al que se le añaden
    #    entradas desde una conversación deja de ser cerrado, que es lo único
    #    que lo hacía útil (ya pasó con «Bogotá»/«BOGOTA»/«Bogotá D.C.»).
    #
    # Lo que sí se ofrece es la LECTURA, y no es un consuelo: calcular cuándo
    # vence un término, saber a qué dependencia repartir, ver con qué
    # vocabulario hay que rellenar un campo y consultar la traza de un objeto
    # son las preguntas que de verdad aparecen mientras se tramita.
    Tool(
        name="calcular_dias_habiles",
        description=(
            "Fecha de vencimiento contando DÍAS HÁBILES desde una fecha, según el "
            "calendario de festivos de la entidad. Es la forma correcta de responder "
            "«¿cuándo vence?»: contar días corridos da una fecha equivocada, y los "
            "términos de ley (p. ej. 15 días hábiles de una petición) se cuentan así."
        ),
        method="GET",
        path="/api/v1/business-days/calculate",
        # Solo autenticado, a propósito: el cálculo del plazo lo necesita
        # cualquiera que tramite, no un administrador (tenant-service/config.py).
        permission=None,
        write=False,
        epic="E14",
        operacion="Calcular el vencimiento en días hábiles",
        params=(
            Param(
                name="from",
                required=True,
                format="date",
                description="Fecha desde la que se cuenta (AAAA-MM-DD).",
                label="Desde",
            ),
            Param(
                name="days",
                type="integer",
                required=True,
                minimum=0,
                maximum=3650,
                description="Cuántos días hábiles hay que contar.",
                label="Días hábiles",
            ),
        ),
    ),
    Tool(
        name="listar_festivos",
        description=(
            "Festivos registrados por la entidad, opcionalmente de un año. Sirve para "
            "explicar un vencimiento: si el plazo cae más lejos de lo esperado, aquí "
            "está el motivo."
        ),
        method="GET",
        path="/api/v1/config/holidays",
        permission=None,
        write=False,
        epic="E14",
        operacion="Consultar los festivos de la entidad",
        params=(
            Param(
                name="year",
                type="integer",
                minimum=2000,
                maximum=2100,
                description="Año a consultar. Sin él, todos.",
                label="Año",
            ),
        ),
    ),
    Tool(
        name="listar_dependencias",
        description=(
            "Dependencias (unidades organizativas) de la entidad: es a ellas a quien se "
            "reparte y traslada un documento. Devuelve el código —el que piden las "
            "tools de reparto— junto al nombre."
        ),
        method="GET",
        path="/api/v1/dependencias",
        permission=None,
        write=False,
        epic="E14",
        operacion="Listar las dependencias de la entidad",
        params=(
            Param(
                name="activo",
                type="boolean",
                description="Solo las activas (true) o solo las inactivas (false).",
                label="Activas",
            ),
            Param(
                name="parent_id",
                format="uuid",
                description="Solo las hijas de esta dependencia.",
                label="Dependencia padre",
            ),
        ),
    ),
    Tool(
        name="arbol_dependencias",
        description=(
            "Estructura orgánica completa en árbol: cada dependencia con las que "
            "cuelgan de ella. Es la vista que hace falta para decidir a quién escalar."
        ),
        method="GET",
        path="/api/v1/dependencias/tree",
        permission=None,
        write=False,
        epic="E14",
        operacion="Consultar el árbol de dependencias",
        params=(
            Param(
                name="activo",
                type="boolean",
                description="Solo las activas.",
                label="Activas",
            ),
        ),
    ),
    Tool(
        name="consultar_dependencia_por_codigo",
        description=(
            "Una dependencia a partir de su CÓDIGO numérico, que es como la nombran las "
            "tools de reparto y como aparece en el número de radicado."
        ),
        method="GET",
        path="/api/v1/dependencias/by-codigo/{codigo}",
        path_params=("codigo",),
        permission=None,
        write=False,
        epic="E14",
        operacion="Consultar una dependencia por su código",
        params=(
            Param(
                name="codigo",
                type="integer",
                required=True,
                location="path",
                description="Código numérico de la dependencia.",
                label="Código",
            ),
        ),
    ),
    Tool(
        name="consultar_dependencia",
        description="Una dependencia por su identificador.",
        method="GET",
        path="/api/v1/dependencias/{dependencia_id}",
        path_params=("dependencia_id",),
        permission=None,
        write=False,
        epic="E14",
        operacion="Consultar una dependencia",
        params=(_P_DEPENDENCIA_ID,),
    ),
    Tool(
        name="listar_catalogos",
        description=(
            "Nombres de los catálogos de vocabulario controlado de la entidad (tipos de "
            "identificación, medios de recepción, causales…). Es el primer paso para "
            "saber qué valores admite un campo antes de rellenarlo."
        ),
        method="GET",
        path="/api/v1/catalogos",
        permission=None,
        write=False,
        epic="E14",
        operacion="Listar los catálogos de vocabulario",
    ),
    Tool(
        name="listar_items_catalogo",
        description=(
            "Valores admitidos por un catálogo. Estos catálogos son CERRADOS: el valor "
            "hay que elegirlo de aquí, no escribirlo a mano, porque «Bogotá», «BOGOTA» y "
            "«Bogotá D.C.» son tres valores distintos para el sistema y ninguna "
            "búsqueda posterior los reúne. Si el valor que hace falta no está, se pide "
            "al administrador; no se inventa."
        ),
        method="GET",
        path="/api/v1/catalogos/{catalogo}",
        path_params=("catalogo",),
        permission=None,
        write=False,
        epic="E14",
        operacion="Consultar los valores de un catálogo",
        params=(
            Param(
                name="catalogo",
                required=True,
                location="path",
                enum=(
                    "tipos-identificacion",
                    "tipos-remitente",
                    "medios-recepcion",
                    "tipos-anexo",
                    "causales",
                    "formas-envio",
                    "soportes",
                    "mensajes-rapidos",
                ),
                description="Nombre del catálogo.",
                label="Catálogo",
            ),
            Param(
                name="activo",
                type="boolean",
                description="Solo los valores vigentes.",
                label="Vigentes",
            ),
            Param(
                name="ambito",
                description=(
                    "Solo para «causales»: acota al acto (p. ej. la anulación o la "
                    "devolución). En los demás catálogos el backend responde 422 en vez "
                    "de ignorarlo, para que nadie use causales de un acto en otro."
                ),
                label="Ámbito",
            ),
        ),
    ),
    Tool(
        name="listar_parametros",
        description=(
            "Parámetros de configuración de la entidad y su valor actual (formato del "
            "número de radicado, plazos por defecto, etc.). Solo lectura: cambiarlos "
            "altera cómo se comporta el sistema para todo el mundo."
        ),
        method="GET",
        path="/api/v1/config/params",
        permission=None,
        write=False,
        epic="E14",
        operacion="Consultar los parámetros de la entidad",
    ),
    Tool(
        name="consultar_parametro",
        description="El valor de un parámetro concreto de la entidad.",
        method="GET",
        path="/api/v1/config/params/{key}",
        path_params=("key",),
        permission=None,
        write=False,
        epic="E14",
        operacion="Consultar un parámetro de la entidad",
        params=(
            Param(
                name="key",
                required=True,
                location="path",
                description="Clave del parámetro.",
                label="Parámetro",
            ),
        ),
    ),
    Tool(
        name="listar_usuarios_asignables",
        description=(
            "Usuarios activos a los que se puede asignar un documento (identificador y "
            "nombre de usuario, nada más). Es la lectura mínima pensada para elegir "
            "destinatario, y por eso la habilita el permiso de radicación y no el de "
            "administración."
        ),
        method="GET",
        path="/api/v1/auth/users/pickable",
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Listar usuarios a los que asignar",
        params=(_p_size(500),),
    ),
    Tool(
        name="listar_niveles_seguridad",
        description=(
            "Niveles de clasificación de la seguridad de la información que usa la "
            "entidad (de Pública a Reservada, RF-SEG-08). Es el vocabulario con el que "
            "se clasifica un documento."
        ),
        method="GET",
        path="/api/v1/auth/security-levels",
        permission=None,
        write=False,
        epic="E08",
        operacion="Consultar los niveles de clasificación",
    ),
    Tool(
        name="mi_contexto",
        description=(
            "Con qué dependencia está trabajando ahora la persona y entre cuáles puede "
            "moverse. Importa porque la dependencia activa decide desde dónde se radica "
            "y qué bandeja se ve."
        ),
        method="GET",
        path="/api/v1/auth/context",
        permission=None,
        write=False,
        epic="E08",
        operacion="Consultar mi contexto de trabajo",
    ),
    Tool(
        name="mi_clearance",
        description=(
            "Hasta qué nivel de clasificación puede ver esta persona. Lo que esté por "
            "encima no aparece en sus consultas y el sistema no revela que exista, así "
            "que un resultado vacío puede significar «no hay» o «no le corresponde»."
        ),
        method="GET",
        path="/api/v1/auth/clearance",
        permission=None,
        write=False,
        epic="E08",
        operacion="Consultar mi nivel de acceso",
    ),
    Tool(
        name="estado_mi_segundo_factor",
        description=(
            "Si la persona tiene el segundo factor (TOTP) activo y desde cuándo. NUNCA "
            "devuelve el secreto. Sirve para saber, ANTES de intentar una firma, si va a "
            "poder completarla; darla de alta se hace en «Mi perfil»."
        ),
        method="GET",
        path="/api/v1/auth/me/totp",
        permission=None,
        write=False,
        epic="E06",
        operacion="Consultar el estado de mi segundo factor",
    ),
    Tool(
        name="estado_mi_credencial_de_firma",
        description=(
            "Si la persona tiene credencial personal de firma, su vigencia y su estado "
            "de revocación. NUNCA devuelve la llave privada."
        ),
        method="GET",
        path="/api/v1/auth/me/signing-key",
        permission=None,
        write=False,
        epic="E06",
        operacion="Consultar el estado de mi credencial de firma",
    ),
    Tool(
        name="listar_usuarios",
        description="Usuarios de la entidad, con su estado. Solo lectura.",
        method="GET",
        path="/api/v1/auth/users",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Listar los usuarios de la entidad",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="listar_grupos",
        description=(
            "Grupos (roles) de la entidad. Los permisos se conceden al grupo, no a la "
            "persona: para saber qué puede hacer alguien hay que mirar a qué grupos "
            "pertenece."
        ),
        method="GET",
        path="/api/v1/auth/groups",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Listar los grupos de la entidad",
    ),
    Tool(
        name="listar_permisos_del_sistema",
        description=(
            "Catálogo de permisos que existen en el sistema, con su descripción. Es el "
            "vocabulario con el que está escrita la seguridad de la entidad."
        ),
        method="GET",
        path="/api/v1/auth/permissions",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Listar los permisos del sistema",
    ),
    Tool(
        name="permisos_de_grupo",
        description=(
            "Permisos concedidos a un grupo, cada uno con su NIVEL (1 leer, 3 crear y "
            "modificar, 5 total). Tener el permiso y tener el nivel no es lo mismo: un "
            "permiso a nivel 1 deja ver y no deja escribir."
        ),
        method="GET",
        path="/api/v1/auth/groups/{group_id}/permissions",
        path_params=("group_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Consultar los permisos de un grupo",
        params=(_P_GROUP_ID,),
    ),
    Tool(
        name="miembros_de_grupo",
        description="Personas que pertenecen a un grupo.",
        method="GET",
        path="/api/v1/auth/groups/{group_id}/members",
        path_params=("group_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Consultar los miembros de un grupo",
        params=(_P_GROUP_ID, _P_PAGE, _p_size(100)),
    ),
    Tool(
        name="clearance_de_grupo",
        description=(
            "Hasta qué nivel de clasificación alcanza un grupo. Si no hay nada "
            "declarado, el sistema asume el nivel más bajo: nunca abre por omisión."
        ),
        method="GET",
        path="/api/v1/auth/groups/{group_id}/clearance",
        path_params=("group_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Consultar el nivel de acceso de un grupo",
        params=(_P_GROUP_ID,),
    ),
    Tool(
        name="permisos_de_usuario",
        description=(
            "Permisos EFECTIVOS de una persona: la suma de los de todos sus grupos, "
            "quedándose con el nivel más alto de cada uno. Es lo que de verdad se "
            "comprueba al autorizar, y puede no coincidir con lo que dice un solo grupo."
        ),
        method="GET",
        path="/api/v1/auth/users/{user_id}/permissions",
        path_params=("user_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Consultar los permisos efectivos de una persona",
        params=(_P_USER_ID,),
    ),
    Tool(
        name="listar_urd_usuario",
        description=(
            "Unidades de Reparto Documental de una persona: qué dependencias puede "
            "atender, además de la suya. Es lo que explica por qué a alguien le llegan "
            "documentos de otra área."
        ),
        method="GET",
        path="/api/v1/auth/users/{user_id}/urd",
        path_params=("user_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Consultar las unidades de reparto de una persona",
        params=(_P_USER_ID,),
    ),
    Tool(
        name="consultar_auditoria",
        description=(
            "Traza de auditoría de la entidad, de lo más reciente a lo más antiguo, con "
            "filtros. Para seguir un objeto concreto se filtra por 'object_ref' con su "
            "número de radicado o su identificador: eso responde «¿quién hizo qué con "
            "esto y cuándo?», que es la pregunta que exige la Ley 594 de 2000. "
            "Recortada por el nivel de acceso de quien pregunta, y el total también: si "
            "un objeto está por encima, sus asientos no se listan ni se cuentan."
        ),
        method="GET",
        path="/api/v1/audit",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Consultar la traza de auditoría",
        params=(
            Param(
                name="object_ref",
                description=(
                    "Identificador del objeto a seguir (número de radicado, UUID de "
                    "expediente…)."
                ),
                label="Objeto",
            ),
            Param(
                name="object_type",
                description="Clase de objeto (radicado, expediente, lote…).",
                label="Tipo de objeto",
            ),
            Param(name="action", description="Acción registrada.", label="Acción"),
            Param(name="actor", description="Quién actuó.", label="Actor"),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="verificar_cadena_auditoria",
        description=(
            "Recalcula la cadena de hash de la auditoría entera y dice si está íntegra. "
            "Es la prueba de que nadie ha alterado ni borrado un asiento. La respuesta "
            "no lleva contenido: solo si está bien y, si no, en qué asiento se rompe. "
            "No se recorta por nivel de acceso, porque una cadena verificada a medias "
            "siempre daría «rota»."
        ),
        method="GET",
        path="/api/v1/audit/verify",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E08",
        operacion="Verificar la integridad de la auditoría",
    ),
    # ── F10 · Correo institucional ──────────────────────────────────────
    #
    # Este flujo se parte en dos mitades que no se parecen en nada, y el reparto
    # es la decisión:
    #
    # * **La bandeja ES trabajo de ventanilla.** Un correo que entra por el buzón
    #   oficial es una comunicación oficial recibida (Ac. 060/2001 art. 3), y
    #   decidir si se radica, quién lo trabaja y con qué dependencia competente es
    #   exactamente lo que hace una unidad de correspondencia. Se ofrece entera.
    # * **La configuración de los buzones NO.** Registrar la aplicación OAuth de
    #   la entidad, dar de alta una cuenta o canjear una autorización significa
    #   mover credenciales: el `client_secret` del proveedor, la contraseña del
    #   buzón, el código de autorización. Nada de eso puede viajar por un chat.
    #
    # Y hay un tercer caso que tampoco se ofrece por la misma razón: **los buzones
    # PERSONALES**. Su contraseña no se guarda a propósito —viaja en la petición y
    # muere con ella—, así que trabajarlos exige teclearla cada vez. El backend
    # responde `password_requerida` y la vía es la pantalla.
    Tool(
        name="listar_buzones_correo",
        description=(
            "Buzones de correo que esta persona puede trabajar, con su identificador. "
            "Es el punto de partida del flujo: el resto de herramientas de correo piden "
            "ese identificador. Devuelve solo los buzones a su alcance —el personal es "
            "de su dueño, el institucional de los funcionarios asignados—, y NUNCA "
            "ninguna credencial."
        ),
        method="GET",
        path="/api/v1/email/cuentas",
        # `PERM_RADI`, el mismo que la bandeja. Este listado exigía
        # `USUA_PERM_ADMIN` y era la única forma de saber qué buzones existen: el
        # funcionario al que la bandeja está dirigida no podía llegar a ella.
        # Corregido en `document-service/app/routers/ingest.py` con su test.
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E19",
        operacion="Listar los buzones de correo que puedo trabajar",
    ),
    Tool(
        name="listar_correos_pendientes",
        description=(
            "Correos de un buzón, del más antiguo al más reciente por FECHA DE RECIBO "
            "—una ventanilla atiende por orden de llegada, y la llegada es la del correo "
            "al buzón, no la del sondeo que lo trajo—. Por defecto los pendientes; "
            "también se pueden ver los ya radicados o los descartados."
        ),
        method="GET",
        path="/api/v1/email/cuentas/{cuenta_id}/bandeja",
        path_params=("cuenta_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E19",
        operacion="Ver los correos de un buzón",
        params=(
            _P_CUENTA_ID,
            Param(
                name="estado",
                enum=("pendiente", "radicado", "descartado"),
                description="Qué correos traer. Por defecto, los pendientes.",
                label="Estado",
            ),
        ),
    ),
    Tool(
        name="estado_bandeja_correo",
        description=(
            "Resumen barato de un buzón: cuántos correos hay pendientes, cuántos está "
            "trabajando alguien y cuándo fue la última novedad. Responde «¿hay algo por "
            "atender?» sin traerse la lista entera."
        ),
        method="GET",
        path="/api/v1/email/cuentas/{cuenta_id}/bandeja/estado",
        path_params=("cuenta_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=False,
        epic="E19",
        operacion="Consultar el resumen de un buzón",
        params=(_P_CUENTA_ID,),
    ),
    Tool(
        name="sincronizar_buzon",
        description=(
            "Trae del servidor de correo los mensajes que aún no están en la bandeja. "
            "NO radica nada: solo trae. Qué se radica y con qué número lo decide una "
            "persona después, correo a correo. Es idempotente: volver a ejecutarlo no "
            "duplica lo que ya está."
        ),
        method="POST",
        path="/api/v1/email/cuentas/{cuenta_id}/bandeja/sincronizar",
        path_params=("cuenta_id",),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        # Traer correos no consume ningún número ni decide nada sobre ellos, y
        # repetirlo es inofensivo. Poner aquí la fricción del acto irreversible
        # sería gastarla donde no hay nada que lamentar.
        destructive=False,
        epic="E19",
        operacion="Traer los correos nuevos de un buzón",
        # `password` NO se declara. La ruta la admite para los buzones
        # personales, cuya credencial no se guarda; pedirla por aquí la dejaría
        # escrita en el historial de la conversación. Sin ella, un buzón personal
        # responde `password_requerida` y la vía es la pantalla — que es la
        # respuesta correcta, no un fallo.
        params=(_P_CUENTA_ID,),
    ),
    Tool(
        name="reservar_correo",
        description=(
            "Toma un correo para trabajarlo, de modo que nadie más lo radique al mismo "
            "tiempo. Si otro lo tiene, responde 409 «correo ocupado» — que no es una "
            "avería sino la reserva haciendo su trabajo. Hay que reservar ANTES de "
            "radicar: sin reserva vigente, radicar se rechaza."
        ),
        method="POST",
        path="/api/v1/email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/reservar",
        path_params=("cuenta_id", "mensaje_id"),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E19",
        operacion="Reservar un correo para trabajarlo",
        params=(_P_CUENTA_ID, _P_MENSAJE_ID),
    ),
    Tool(
        name="liberar_correo",
        description=(
            "Suelta la reserva propia sin decidir nada sobre el correo: vuelve a quedar "
            "disponible para la ventanilla. Es el acto de ARREPENTIRSE, y por eso no "
            "pide confirmación: parar tiene que ser barato."
        ),
        method="POST",
        path="/api/v1/email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/liberar",
        path_params=("cuenta_id", "mensaje_id"),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=False,
        epic="E19",
        operacion="Liberar la reserva de un correo",
        params=(_P_CUENTA_ID, _P_MENSAJE_ID),
    ),
    Tool(
        name="radicar_correo",
        description=(
            "Convierte un correo reservado en un radicado de ENTRADA, con el mensaje "
            "original y sus adjuntos como soportes. Consume un número de radicado, que "
            "es inmutable y no se reutiliza; y sobre una Entrada la anulación no "
            "existe, así que radicar el correo equivocado no se deshace. Exige reserva "
            "vigente propia."
        ),
        method="POST",
        path="/api/v1/email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/radicar",
        path_params=("cuenta_id", "mensaje_id"),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        destructive=True,
        # NO se pide teclear el identificador del mensaje. Sería un UUID que la
        # persona no puede contrastar con nada —el mismo motivo por el que no se
        # ofrece sellar un artefacto en WORM—: enseñaría rigor sin darlo. La
        # tarjeta muestra el asunto y el remitente, que sí se contrastan.
        epic="E19",
        operacion="Radicar un correo como documento de entrada",
        params=(
            _P_CUENTA_ID,
            _P_MENSAJE_ID,
            Param(
                name="subject",
                description=(
                    "Asunto del radicado. Si se omite, se toma el del correo; se corrige "
                    "cuando el remitente lo escribió mal."
                ),
                label="Asunto",
            ),
            Param(
                name="sender_name",
                description="Remitente. Si se omite, el que trae el correo.",
                label="Remitente",
            ),
            Param(name="sender_entity", description="Entidad remitente.", label="Entidad"),
            Param(
                name="dest_dept_code",
                type="integer",
                minimum=1,
                maximum=999999,
                description=(
                    "CÓDIGO de la dependencia competente, del organigrama. Se obtiene con "
                    "'listar_dependencias'; no se escribe el nombre a mano, porque un "
                    "nombre suelto no vincula el radicado con la dependencia y no "
                    "aparecería al repartir."
                ),
                label="Dependencia competente",
            ),
            Param(
                name="doc_class",
                description=(
                    "Código del tipo documental, del catálogo ('listar_tipos_documentales'). "
                    "Gobierna la retención según la TRD; puede tipificarse después."
                ),
                label="Tipo documental",
            ),
        ),
    ),
    Tool(
        name="descartar_correo",
        description=(
            "Aparta un correo que llegó al buzón y no debía radicarse, dejando "
            "constancia del motivo. No borra el correo del servidor: lo saca de la "
            "bandeja. Es una decisión sobre una comunicación oficial recibida y la "
            "unidad de correspondencia responde de ella (Ac. 060/2001 art. 3)."
        ),
        method="POST",
        path="/api/v1/email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/descartar",
        path_params=("cuenta_id", "mensaje_id"),
        permission="PERM_RADI",
        min_crud=1,
        write=True,
        # Saca de la bandeja una comunicación oficial recibida y no hay acto que
        # lo devuelva a «pendiente»: el estado solo avanza.
        destructive=True,
        epic="E19",
        operacion="Descartar un correo sin radicarlo",
        params=(
            _P_CUENTA_ID,
            _P_MENSAJE_ID,
            Param(
                name="motivo",
                required=True,
                min_length=5,
                description="Por qué no se radica. Queda en la traza.",
                label="Motivo",
            ),
        ),
    ),
    # ── F13 · Notificaciones e integración ──────────────────────────────
    #
    # Se ofrece **observar** y **desatascar**; no se ofrece **configurar**.
    #
    # Un webhook manda los eventos documentales de la entidad a una URL de fuera:
    # crearlo es abrir un canal de salida permanente, y eso no se decide en una
    # conversación. La cola de fallos, en cambio, es reconciliación operativa con
    # traza completa, y dejarla solo mirable convertiría la mitad de la capacidad
    # en decoración.
    Tool(
        name="historial_notificaciones",
        description=(
            "Últimas notificaciones que el sistema envió: a quién, por qué y si "
            "salieron. Responde «¿le llegó el aviso al ciudadano?», que es la pregunta "
            "que aparece cuando alguien reclama que no le avisaron."
        ),
        method="GET",
        path="/api/v1/notifications/history",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E16",
        operacion="Ver el historial de notificaciones",
        params=(
            Param(
                name="limit",
                type="integer",
                minimum=1,
                maximum=100,
                description="Cuántas traer (máx. 100).",
                label="Cantidad",
                show_in_card=False,
            ),
        ),
    ),
    Tool(
        name="consultar_notificacion",
        description="Una notificación concreta: destinatario, asunto, estado y momento.",
        method="GET",
        path="/api/v1/notifications/{notification_id}",
        path_params=("notification_id",),
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E16",
        operacion="Consultar una notificación",
        params=(
            Param(
                name="notification_id",
                required=True,
                location="path",
                format="uuid",
                description="UUID de la notificación.",
                label="Notificación",
            ),
        ),
    ),
    Tool(
        name="listar_webhooks",
        description=(
            "Canales de salida configurados: a qué URL de fuera se mandan los eventos "
            "documentales de la entidad y qué eventos. Solo lectura — dar de alta o "
            "retirar un canal no se hace por este medio."
        ),
        method="GET",
        path="/api/v1/webhooks",
        permission="USUA_PERM_ADMIN",
        min_crud=1,
        write=False,
        epic="E11",
        operacion="Listar los canales de salida configurados",
    ),
    Tool(
        name="listar_eventos_atascados",
        description=(
            "Cola de fallos: eventos que el sistema no consiguió procesar tras sus "
            "reintentos. Cada uno es una consecuencia que no llegó a ocurrir —un aviso "
            "que no salió, una copia que no se actualizó—, así que una cola que crece "
            "es una señal, no un detalle técnico."
        ),
        method="GET",
        path="/api/v1/notification/admin/deadletter/",
        permission="PERM_DLQ_ADMIN",
        min_crud=1,
        write=False,
        epic="E16",
        operacion="Ver la cola de eventos fallidos",
        params=(
            Param(
                name="status",
                enum=("pending", "resolved", "discarded"),
                description="Qué eventos traer. Por defecto, los pendientes.",
                label="Estado",
            ),
            _P_PAGE,
            _p_size(100),
        ),
    ),
    Tool(
        name="consultar_evento_atascado",
        description=(
            "Un evento de la cola de fallos con su contenido y el error que lo dejó "
            "ahí: lo que hace falta para decidir si se reintenta o se descarta."
        ),
        method="GET",
        path="/api/v1/notification/admin/deadletter/{event_id}",
        path_params=("event_id",),
        permission="PERM_DLQ_ADMIN",
        min_crud=1,
        write=False,
        epic="E16",
        operacion="Consultar un evento fallido",
        params=(_P_EVENT_ID,),
    ),
    Tool(
        name="reintentar_evento_atascado",
        description=(
            "Vuelve a procesar un evento de la cola de fallos. El efecto es EXTERNO y "
            "no transaccional —se manda correo—, así que si el fallo original ocurrió "
            "después de un envío parcial, este reintento puede duplicar avisos ya "
            "recibidos. Se prefiere ese riesgo al de perder un aviso en silencio, y por "
            "eso se declara aquí."
        ),
        method="POST",
        path="/api/v1/notification/admin/deadletter/{event_id}/replay",
        path_params=("event_id",),
        permission="PERM_DLQ_ADMIN",
        min_crud=1,
        write=True,
        # Un correo enviado no se recoge.
        destructive=True,
        epic="E16",
        operacion="Reintentar un evento fallido",
        params=(_P_EVENT_ID,),
    ),
    Tool(
        name="descartar_evento_atascado",
        description=(
            "Renuncia deliberadamente a procesar un evento de la cola, dejando "
            "constancia del motivo. Lo que ese evento iba a producir —un aviso, una "
            "actualización— NO va a ocurrir, y no hay acto que lo devuelva a la cola."
        ),
        method="POST",
        path="/api/v1/notification/admin/deadletter/{event_id}/discard",
        path_params=("event_id",),
        permission="PERM_DLQ_ADMIN",
        min_crud=1,
        write=True,
        destructive=True,
        epic="E16",
        operacion="Descartar un evento fallido",
        params=(
            _P_EVENT_ID,
            Param(
                name="reason",
                required=True,
                min_length=10,
                description=(
                    "Por qué se renuncia a procesarlo. Queda auditado, y por eso el "
                    "backend exige un motivo sustantivo, no un carácter suelto."
                ),
                label="Motivo",
            ),
        ),
    ),
    # ── Firma en cadena (E06, RF-FIR-04/12/13) ──────────────────────────────
    #
    # El circuito de firma estaba ENTERO sin exponer: trece endpoints, cero
    # tools. El asistente no podía contestar «¿qué tengo pendiente de firmar?»,
    # que es la pregunta más frecuente que hay sobre él.
    #
    # Lo que se ofrece es LEER y PREPARAR; firmar no. El motivo ya estaba
    # decidido en `scope.py` para la firma del acta de transferencia —«un acto
    # de identidad con segundo factor: pedirlo por un chat trasladaría el código
    # de un solo uso a un canal que no lo custodia»— y vale igual aquí: `sign`
    # consume un OTP o un TOTP. Se declara en `NO_OFRECIDAS`, no se omite en
    # silencio, para que quien pregunte sepa por dónde sí.
    #
    # Todo el router exige `PERM_FIRMA` sin nivel mínimo (`require_permission`
    # con su default), así que `min_crud=1` incluso en la escritura: exigir 3
    # aquí escondería la tool a quien el backend sí deja abrir una cadena — el
    # defecto que falla en silencio.
    Tool(
        name="firmas_pendientes",
        description=(
            "Documentos que esperan TU firma ahora mismo: solo los turnos que te "
            "tocan a ti, no la cadena entera. Un turno no aparece hasta que el "
            "anterior se resuelve, así que una cadena en la que firmas tercero no "
            "se ve hasta que los dos primeros han firmado."
        ),
        method="GET",
        path="/api/v1/signature/pending",
        permission="PERM_FIRMA",
        min_crud=1,
        write=False,
        epic="E06",
        operacion="Ver qué tengo pendiente de firmar",
        params=(_P_PAGE, _p_size(100)),
    ),
    Tool(
        name="consultar_cadena_firma",
        description=(
            "Estado de una cadena de firmas: sus turnos EN ORDEN, quién ha "
            "resuelto el suyo y a quién le toca. Sirve para responder «¿por qué "
            "no me ha llegado?» — normalmente porque el turno anterior sigue "
            "abierto."
        ),
        method="GET",
        path="/api/v1/signature/requests/{solicitud_id}",
        path_params=("solicitud_id",),
        permission="PERM_FIRMA",
        min_crud=1,
        write=False,
        epic="E06",
        operacion="Consultar una cadena de firmas",
        params=(_P_SOLICITUD_ID,),
    ),
    Tool(
        name="cadena_firma_de_un_objeto",
        description=(
            "La cadena ABIERTA sobre un documento, si la hay. Devuelve 404 cuando "
            "no hay ninguna, y eso es una respuesta y no un error: es la forma de "
            "saber si un radicado o un expediente ya está en circuito antes de "
            "abrirle otro."
        ),
        method="GET",
        path="/api/v1/signature/requests",
        permission="PERM_FIRMA",
        min_crud=1,
        write=False,
        epic="E06",
        operacion="Ver si un documento ya está en un circuito de firma",
        params=(_P_OBJETO_TIPO, _P_OBJETO_ID),
    ),
    Tool(
        name="consultar_comprobante_firma",
        description=(
            "Metadatos de una firma ya producida: cuándo, con qué proveedor y con "
            "qué nivel de conformidad. NO acredita: esta firma es electrónica "
            "simple (Ley 527/1999 art. 7, no art. 28) y el comprobante lo dice."
        ),
        method="GET",
        path="/api/v1/signature/firmas/{firma_id}",
        path_params=("firma_id",),
        # Sin permiso: el endpoint tampoco lo pide. Declarar `PERM_FIRMA` aquí
        # —como hice al escribirla— la habría escondido a quien el backend sí
        # deja consultarla, y lo cazó `test_permisos_declarados`. La respuesta es
        # content-free por diseño (no lleva el certificado, solo su huella), y
        # llegar hasta aquí ya exige una sesión válida en el gateway.
        permission=None,
        min_crud=1,
        write=False,
        epic="E06",
        operacion="Ver el comprobante de una firma",
        params=(_P_FIRMA_ID,),
    ),
    Tool(
        name="abrir_cadena_firma",
        description=(
            "Abre un circuito de firmas sobre un documento: varias personas firman "
            "EN ORDEN y la posición en la lista es el turno. Abrirlo no firma nada "
            "ni compromete a nadie — cada firmante decide cuando le toca, y puede "
            "rechazar. Sobre un radicado anulado no se abre."
        ),
        method="POST",
        path="/api/v1/signature/requests",
        permission="PERM_FIRMA",
        min_crud=1,
        write=True,
        # Reversible y sin compromiso: el turno se puede rechazar, y mientras
        # nadie firme no hay acto jurídico ninguno. Lo irreversible es firmar, y
        # eso no se ofrece por este canal.
        destructive=False,
        epic="E06",
        operacion="Abrir un circuito de firmas",
        fallback=(
            "Sin permiso de firma, puede pedirse la apertura a quien deba firmar, "
            "o abrirla desde la ficha del expediente o el radicado"
        ),
        params=(
            _P_OBJETO_TIPO,
            _P_OBJETO_ID,
            Param(
                name="titulo",
                description="Título con el que los firmantes lo verán en su bandeja.",
                label="Título",
            ),
            Param(
                name="firmantes",
                type="array",
                required=True,
                description=(
                    "UUID de cada firmante, EN EL ORDEN en que deben firmar: el "
                    "primero de la lista es el turno 1. Una misma persona no ocupa "
                    "dos turnos."
                ),
                label="Firmantes (en orden)",
            ),
        ),
    ),
)


class ArgError(ValueError):
    """Argumento inválido o no declarado en el catálogo."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def json_schema(tool: Invocable) -> dict[str, Any]:
    """JSON Schema de los argumentos. Sirve a MCP y a Anthropic sin adaptador.

    `additionalProperties: False` es solo advisory para el modelo —no lo aplica
    nadie server-side—, así que la barrera de verdad es `coerce_args`.
    """
    return {
        "type": "object",
        "properties": {p.name: p.json_schema() for p in tool.params},
        "required": [p.name for p in tool.params if p.required],
        "additionalProperties": False,
    }


_TIPOS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def coerce_args(tool: Invocable, raw: dict[str, Any]) -> dict[str, Any]:
    """Valida y acota los argumentos contra lo declarado.

    **Rechaza** las claves no declaradas en vez de descartarlas en silencio: el
    modelo recibe el error y se corrige, y un intento de inyección deja rastro en
    vez de evaporarse. La versión anterior las filtraba sin decir nada, que es
    cómodo hasta el día que quien cuela una clave es un radicado de Entrada.
    """
    declarados = tool.params_by_name

    for clave in raw:
        if clave not in declarados:
            raise ArgError(
                "parametro_no_declarado",
                f"'{clave}' no es un argumento de '{tool.name}'. "
                f"Admitidos: {', '.join(sorted(declarados)) or '(ninguno)'}",
            )

    limpio: dict[str, Any] = {}
    for p in tool.params:
        if p.name not in raw or raw[p.name] is None:
            if p.required:
                raise ArgError("falta_parametro", f"falta el argumento obligatorio '{p.name}'")
            continue

        valor = raw[p.name]
        esperado = _TIPOS.get(p.type, str)
        # `bool` es subclase de `int` en Python: sin esta guarda, `True` pasaría
        # por un entero válido y viajaría como `true` a un campo numérico.
        if p.type in ("integer", "number") and isinstance(valor, bool):
            raise ArgError("tipo_invalido", f"'{p.name}' espera {p.type}, no un booleano")
        if not isinstance(valor, esperado):
            raise ArgError("tipo_invalido", f"'{p.name}' espera {p.type}")

        if p.enum is not None and valor not in p.enum:
            raise ArgError(
                "valor_invalido", f"'{p.name}' admite: {', '.join(p.enum)}"
            )
        if isinstance(valor, str):
            if p.min_length is not None and len(valor) < p.min_length:
                raise ArgError("valor_invalido", f"'{p.name}' exige {p.min_length} caracteres o más")
            if p.max_length is not None and len(valor) > p.max_length:
                raise ArgError("valor_invalido", f"'{p.name}' admite {p.max_length} caracteres como máximo")
            if p.format == "uuid" and not _UUID_RE.match(valor):
                # Vale por sí mismo (un 422 del backend es peor mensaje) y además
                # es la primera capa contra el traversal por parámetro de ruta.
                raise ArgError("valor_invalido", f"'{p.name}' debe ser un UUID")
        if isinstance(valor, int | float) and not isinstance(valor, bool):
            if p.minimum is not None and valor < p.minimum:
                raise ArgError("valor_invalido", f"'{p.name}' no puede ser menor que {p.minimum}")
            if p.maximum is not None and valor > p.maximum:
                raise ArgError("valor_invalido", f"'{p.name}' no puede ser mayor que {p.maximum}")

        limpio[p.name] = valor

    for grupo in tool.exactly_one_of:
        presentes = [n for n in grupo if n in limpio]
        if len(presentes) != 1:
            recibido = ", ".join(presentes) if presentes else "ninguno"
            raise ArgError(
                "parametros_excluyentes",
                f"'{tool.name}' exige exactamente uno de: {', '.join(grupo)}. "
                f"Se recibió: {recibido}",
            )
    return limpio


def card_fields(tool: Invocable, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Campos legibles del acto, para la tarjeta de confirmación.

    Una LISTA se resume por su tamaño y no se vuelca. Un lote de eliminación
    lleva los identificadores de los expedientes que va a destruir: volcar
    cuarenta UUID en la tarjeta no informa, **tapa** — quien tiene que decidir
    acaba mirando un muro de texto en vez de la única cifra que importa, que es
    cuántos. El detalle sigue viajando en la propuesta firmada; lo que cambia es
    qué se le pone delante a la persona.
    """
    campos: list[dict[str, Any]] = []
    for p in tool.params:
        if not (p.show_in_card and p.name in args):
            continue
        valor = args[p.name]
        if isinstance(valor, list):
            campos.append(
                {
                    "arg": p.name,
                    "etiqueta": p.etiqueta,
                    "valor": f"{len(valor)} elemento{'s' if len(valor) != 1 else ''}",
                    "tipo": p.type,
                }
            )
            continue
        campos.append({"arg": p.name, "etiqueta": p.etiqueta, "valor": valor, "tipo": p.type})
    return campos


_BY_NAME: dict[str, Tool] = {t.name: t for t in CATALOG}


def get_tool(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def puede_usar(
    tool: Invocable,
    permissions: set[str],
    *,
    is_root: bool = False,
    levels: dict[str, int] | None = None,
) -> bool:
    """¿Este usuario alcanza esta tool? Presencia del permiso **y** nivel CRUD.

    `levels` es el `permission_levels` de `GET /auth/me` (permiso → crud 0..5).
    Cuando no se aporta —llamantes antiguos que solo tienen la lista plana— se
    comprueba solo la presencia: es el comportamiento previo, y degradar a
    «no puede» dejaría sin tools a quien sí las tiene. Cuando SÍ se aporta, el
    permiso ausente vale 0, que es fail-closed.
    """
    if is_root:
        return True
    if tool.permission is None:
        return True
    if tool.permission not in permissions:
        return False
    if levels is None:
        return True
    return levels.get(tool.permission, 0) >= tool.min_crud


def visible_tools(
    permissions: set[str],
    is_root: bool = False,
    levels: dict[str, int] | None = None,
) -> list[Tool]:
    """Tools visibles para un usuario según sus permisos (ROOT ve todas)."""
    return [t for t in CATALOG if puede_usar(t, permissions, is_root=is_root, levels=levels)]
