# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B9-1 (ADR-035): todo `get_tenant_conn` mira el estado de la entidad ANTES del schema.

Solo document-service comprobaba `public.tenants.status`; los otros cinco `get_tenant_conn`
fijaban el `search_path` sin mirar, así que suspender una entidad no le retiraba el acceso
y un schema inexistente acababa en `UndefinedTableError` (500). Un guardián por FORMA
(AST): cada `get_tenant_conn` definido en un servicio tiene que esperar a
`veredicto_entidad`/`estado_entidad` y hacerlo antes de cualquier `SET search_path`. La
lista esperada se declara: un `get_tenant_conn` nuevo obliga a decidirlo, no pasa de largo.
"""

import ast
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_SERVICIOS = _RAIZ / "services"

_necesita_arbol = pytest.mark.skipif(
    not _SERVICIOS.is_dir(), reason="el árbol de servicios no está montado"
)

#: Servicios que definen `get_tenant_conn` (archive, workflow y storage fijan el
#: `search_path` en repositorios; los cubre el gate del api-gateway, ver ADR-035).
ESPERADOS = ["auth", "document", "knowledge", "notification", "signature", "tenant"]
_LLAMADAS_AL_ESTADO = {"veredicto_entidad", "estado_entidad", "atencion_publica"}


def _get_tenant_conn(servicio: str) -> ast.AsyncFunctionDef | None:
    archivo = _SERVICIOS / f"{servicio}-service" / "app" / "core" / "database.py"
    for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.AsyncFunctionDef) and nodo.name == "get_tenant_conn":
            return nodo
    return None


def _primera_linea(nodo: ast.AST, predicado) -> int | None:
    lineas = [n.lineno for n in ast.walk(nodo) if predicado(n)]
    return min(lineas) if lineas else None


def _es_await_de_estado(n: ast.AST) -> bool:
    return (
        isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id in _LLAMADAS_AL_ESTADO
    )


def _es_set_search_path(n: ast.AST) -> bool:
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value.lstrip().startswith("SET search_path")
    if isinstance(n, ast.JoinedStr):
        primero = n.values[0] if n.values else None
        return (
            isinstance(primero, ast.Constant)
            and isinstance(primero.value, str)
            and primero.value.lstrip().startswith("SET search_path")
        )
    return False


@_necesita_arbol
def test_la_lista_de_servicios_con_get_tenant_conn_es_la_declarada() -> None:
    definen = sorted(
        d.parent.parent.parent.name.removesuffix("-service")
        for d in _SERVICIOS.glob("*/app/core/database.py")
        if "async def get_tenant_conn" in d.read_text(encoding="utf-8")
    )
    assert definen == ESPERADOS, (
        "un `get_tenant_conn` nuevo (o retirado) debe pasar por el gate del estado de la "
        "entidad y declararse aquí"
    )


@_necesita_arbol
@pytest.mark.parametrize("servicio", ESPERADOS)
def test_get_tenant_conn_mira_el_estado_antes_de_fijar_el_schema(servicio: str) -> None:
    fn = _get_tenant_conn(servicio)
    assert fn is not None, f"{servicio}: no define get_tenant_conn"

    estado = _primera_linea(fn, _es_await_de_estado)
    schema = _primera_linea(fn, _es_set_search_path)

    assert estado is not None, f"{servicio}: get_tenant_conn no espera al estado de la entidad"
    assert schema is not None, f"{servicio}: no se encontró el SET search_path"
    assert estado < schema, f"{servicio}: el estado debe leerse ANTES del SET search_path"


@_necesita_arbol
def test_el_gate_no_se_traga_el_veredicto() -> None:
    """`veredicto_entidad` devuelve el veto; quien lo llama debe LEVANTARLO. Un
    `await veredicto_entidad(...)` cuyo resultado se descarta no protege nada."""
    for servicio in ESPERADOS:
        fn = _get_tenant_conn(servicio)
        assert fn is not None
        usos = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.NamedExpr)  # `(veto := await veredicto_entidad(...))`
            and isinstance(n.value, ast.Await)
        ]
        assert usos, f"{servicio}: el veredicto debe asignarse y evaluarse (walrus)"
        levanta = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
        assert levanta, f"{servicio}: get_tenant_conn no levanta nada ante un veto"


# --------------------------------------------------------------------------- censo por FORMA
#
# El guardián de arriba busca `get_tenant_conn` por NOMBRE y solo en `app/core/database.py`:
# un servicio nuevo que ceda la conexión con otro nombre (`get_conn`, `tenant_session`) o en
# otro módulo pasaría de largo sin que nadie lo decidiera. Este censo busca por FORMA: toda
# función generadora que fije el `search_path` a un schema de TENANT (no el `public` de la
# limpieza) y ceda la conexión. Lo que aparezca y no esté declarado obliga a decidir.

#: Generadores de conexión de PETICIÓN o de ruta pública: tienen que mirar el estado.
CON_GATE = {
    ("auth", "get_tenant_conn"),
    ("document", "get_tenant_conn"),
    ("document", "public_tenant_conn_atencion"),
    ("knowledge", "get_tenant_conn"),
    ("notification", "get_tenant_conn"),
    ("signature", "get_tenant_conn"),
    ("tenant", "get_tenant_conn"),
}

#: Exentos CON MOTIVO (ADR-035): no son la puerta de una petición.
#:  - `acquire_tenant`: tarea en segundo plano que lanzó una petición YA autorizada, o
#:    consumidor de eventos (convergencia interna: bloquearlo con la entidad suspendida
#:    dejaría el ACL/nivel de copias derivadas por debajo del real al reactivar).
#:  - `auth.get_optional_tenant_conn`: `/me`, cubierto por el gateway; su contrato de
#:    «vacíos» es deliberado (B7-F3) y ya resuelve el schema ausente con `to_regclass`.
EXENTOS = {
    ("archive", "acquire_tenant"),
    ("document", "acquire_tenant"),
    ("notification", "acquire_tenant"),
    ("signature", "acquire_tenant"),
    ("workflow", "acquire_tenant"),
    ("auth", "get_optional_tenant_conn"),
}


def _fija_schema_de_tenant(n: ast.AST) -> bool:
    """`SET search_path = "{schema}"` (interpolado), no el reset a `public`."""
    if isinstance(n, ast.JoinedStr):
        return _es_set_search_path(n) and any(
            isinstance(v, ast.FormattedValue) for v in n.values
        )
    return False


def _cede_conexion_de_tenant(fn: ast.AsyncFunctionDef) -> bool:
    cede = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))
    return cede and any(_fija_schema_de_tenant(n) for n in ast.walk(fn))


def _censo() -> set[tuple[str, str]]:
    hallados: set[tuple[str, str]] = set()
    for archivo in _SERVICIOS.glob("*-service/app/**/*.py"):
        if "ops" in archivo.parts or "jobs" in archivo.parts:
            continue  # herramientas de operador y jobs: no atienden peticiones
        servicio = archivo.relative_to(_SERVICIOS).parts[0].removesuffix("-service")
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.AsyncFunctionDef) and _cede_conexion_de_tenant(nodo):
                hallados.add((servicio, nodo.name))
    return hallados


@_necesita_arbol
def test_todo_generador_de_conexion_de_tenant_esta_declarado() -> None:
    hallados = _censo()
    assert len(hallados) >= 10, "el censo salió corto: el guardián vigilaría el vacío"
    sin_decidir = hallados - CON_GATE - EXENTOS
    assert not sin_decidir, (
        f"generadores de conexión de tenant sin decidir (¿miran el estado de la entidad?): "
        f"{sorted(sin_decidir)}. Añádelos a CON_GATE o a EXENTOS con su motivo."
    )
    desaparecidos = (CON_GATE | EXENTOS) - hallados
    assert not desaparecidos, f"declarados y ya inexistentes: {sorted(desaparecidos)}"


@_necesita_arbol
def test_todo_generador_con_gate_mira_el_estado_antes_del_schema() -> None:
    for servicio, nombre in sorted(CON_GATE):
        fn = next(
            (
                n
                for a in (_SERVICIOS / f"{servicio}-service" / "app").rglob("*.py")
                for n in ast.walk(ast.parse(a.read_text(encoding="utf-8")))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == nombre
            ),
            None,
        )
        assert fn is not None, f"{servicio}.{nombre}: no existe"
        estado = _primera_linea(fn, _es_await_de_estado)
        schema = _primera_linea(fn, _es_set_search_path)
        assert estado is not None and schema is not None and estado < schema, (
            f"{servicio}.{nombre}: debe esperar al estado de la entidad ANTES del SET search_path"
        )
