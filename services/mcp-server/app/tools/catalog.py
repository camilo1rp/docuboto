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
_P_PAGE = Param(
    name="page",
    type="integer",
    minimum=1,
    description="Página, 1-indexada.",
    label="Página",
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
        name="consultar_expediente",
        description="Consulta un expediente por su id (incluye radicados vinculados).",
        method="GET",
        path="/api/v1/expedientes/{expediente_id}",
        path_params=("expediente_id",),
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
