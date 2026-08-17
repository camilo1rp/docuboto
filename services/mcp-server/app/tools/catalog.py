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
from typing import Any

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
        min_crud=1,
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
        min_crud=1,
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
        min_crud=1,
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
)


class ArgError(ValueError):
    """Argumento inválido o no declarado en el catálogo."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def json_schema(tool: Tool) -> dict[str, Any]:
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


def coerce_args(tool: Tool, raw: dict[str, Any]) -> dict[str, Any]:
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


def card_fields(tool: Tool, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Campos legibles del acto, para la tarjeta de confirmación."""
    return [
        {"arg": p.name, "etiqueta": p.etiqueta, "valor": args[p.name], "tipo": p.type}
        for p in tool.params
        if p.show_in_card and p.name in args
    ]


_BY_NAME: dict[str, Tool] = {t.name: t for t in CATALOG}


def get_tool(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def puede_usar(
    tool: Tool,
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
