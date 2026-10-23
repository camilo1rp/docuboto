# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que el censo del MCP publica como «excluido a propósito» es cierto (C22).

`scripts/censo_mcp.py` separa lo que se DECIDIÓ no ofrecer (`EXCLUIDAS_A_PROPOSITO`)
de lo que simplemente falta, y calcula la cobertura sobre `operaciones − excluidas`.
Esa resta es una afirmación: si una exclusión ya no casa con ninguna ruta, no excluye
nada y el hueco que ocultaba sigue oculto; si casa con una ruta que SÍ tiene
herramienta, la lista y el catálogo se contradicen; y si un acto de `NO_OFRECIDAS`
(lo que el asistente le dice a la persona) no figura en la lista, el censo cuenta como
hueco algo que el producto declara decidido. La lista no puede ser una opinión: aquí
se cruza con el árbol.

**Qué vigila** (todo por lectura estática; sin servicios ni base de datos):

1. cada entrada casa con al menos una `@router.<método>("…")` real;
2. las `SELLADA:` casan solo con rutas que llevan `require_internal_token`;
3. toda referencia `NO_OFRECIDAS: <operación>` existe en `scope.py`, y toda operación
   de `NO_OFRECIDAS` tiene una exclusión o una razón declarada de no tener ruta;
4. ninguna ruta excluida tiene herramienta en `CATALOG`;
5. cada referencia a un fichero apunta a un fichero que existe.

Cada comprobación va como función pura con un control positivo: se ejecuta sobre una
lista sintética rota y debe acusarla (un guardián que nunca ha visto fallar no
demuestra nada).
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

RAIZ = Path(__file__).resolve().parents[2]
_SCOPE = RAIZ / "services" / "mcp-server" / "app" / "services" / "scope.py"
_METODOS = frozenset({"get", "post", "put", "patch", "delete"})
_ARCHIVOS_EXENTOS = frozenset({"health.py", "openapi.py", "metrics.py"})
_SELLOS = frozenset({"require_internal_token", "_require_internal_token"})

Ruta = tuple[str, str, frozenset[str]]  # (MÉTODO, ruta completa, nombres del decorador/firma/router)
Entrada = tuple[str, str, str, str]  # (método, regex de ruta, motivo, referencia)


def _censo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("censo_mcp", RAIZ / "scripts" / "censo_mcp.py")
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _nombres(nodo: ast.AST) -> set[str]:
    hallados: set[str] = set()
    for n in ast.walk(nodo):
        if isinstance(n, ast.Name):
            hallados.add(n.id)
        elif isinstance(n, ast.Attribute):
            hallados.add(n.attr)
    return hallados


def _rutas_de(texto: str) -> list[Ruta]:
    """Las rutas de un router, con el prefijo del `APIRouter` y los nombres que las protegen."""
    arbol = ast.parse(texto)
    routers: dict[str, tuple[str, set[str]]] = {}
    for n in ast.walk(arbol):
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "id", None) == "APIRouter"
            and isinstance(n.targets[0], ast.Name)
        ):
            kw = {k.arg: k.value for k in n.value.keywords}
            prefijo = str(kw["prefix"].value) if isinstance(kw.get("prefix"), ast.Constant) else ""
            deps = _nombres(kw["dependencies"]) if "dependencies" in kw else set()
            routers[n.targets[0].id] = (prefijo, deps)
    salida: list[Ruta] = []
    for fn in ast.walk(arbol):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in fn.decorator_list:
            if not (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id in routers
                and d.func.attr in _METODOS
            ):
                continue
            prefijo, deps = routers[d.func.value.id]
            ruta = str(d.args[0].value) if d.args and isinstance(d.args[0], ast.Constant) else ""
            nombres = _nombres(d) | _nombres(fn.args) | deps
            salida.append((d.func.attr.upper(), f"{prefijo}{ruta}", frozenset(nombres)))
    return salida


def _todas_las_rutas() -> list[Ruta]:
    rutas: list[Ruta] = []
    for f in sorted((RAIZ / "services").glob("*/app/routers/*.py")):
        if f.name not in _ARCHIVOS_EXENTOS:
            rutas.extend(_rutas_de(f.read_text(encoding="utf-8")))
    return rutas


def _no_ofrecidas() -> list[str]:
    """Las `operacion` de `NO_OFRECIDAS`, leídas del AST (scope.py importa httpx y la app)."""
    arbol = ast.parse(_SCOPE.read_text(encoding="utf-8"))
    for n in arbol.body:
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "NO_OFRECIDAS" and n.value:
            return [d["operacion"] for d in ast.literal_eval(n.value)]
    raise AssertionError("scope.py ya no define NO_OFRECIDAS: el guardián vigilaría el vacío")


def _casa(entrada: Entrada, metodo: str, ruta: str) -> bool:
    return bool(re.fullmatch(entrada[0], metodo)) and bool(re.search(entrada[1], ruta.rstrip("/") or "/"))


# ------------------------------------------------------------------ comprobaciones puras


def _sin_ruta(entradas: tuple[Entrada, ...], rutas: list[Ruta]) -> list[str]:
    return [e[1] for e in entradas if not any(_casa(e, m, r) for m, r, _ in rutas)]


def _selladas_sin_sello(entradas: tuple[Entrada, ...], rutas: list[Ruta]) -> list[str]:
    malas = []
    for e in entradas:
        if e[3].startswith("SELLADA: "):
            malas += [f"{m} {r}" for m, r, nombres in rutas if _casa(e, m, r) and not nombres & _SELLOS]
    return malas


def _referencias_rotas(entradas: tuple[Entrada, ...], operaciones: list[str]) -> list[str]:
    rotas = []
    for e in entradas:
        ref = e[3]
        if ref.startswith("NO_OFRECIDAS: "):
            if not any(o.startswith(ref.removeprefix("NO_OFRECIDAS: ")) for o in operaciones):
                rotas.append(ref)
        else:
            fichero = ref.removeprefix("SELLADA: ").split(" ", 1)[0]
            if not (RAIZ / fichero).is_file():
                rotas.append(ref)
    return rotas


def _no_representadas(
    entradas: tuple[Entrada, ...], operaciones: list[str], sin_ruta: dict[str, str]
) -> list[str]:
    """Operaciones de NO_OFRECIDAS sin exclusión que las represente ni razón de no tenerla."""
    citadas = {e[3].removeprefix("NO_OFRECIDAS: ") for e in entradas if e[3].startswith("NO_OFRECIDAS: ")}
    return [o for o in operaciones if not any(o.startswith(c) for c in citadas) and o not in sin_ruta]


def _excluidas_con_herramienta(entradas: tuple[Entrada, ...], catalogo: set[tuple[str, str]]) -> list[str]:
    return [f"{m} {r}" for m, r in sorted(catalogo) if any(_casa(e, m, r) for e in entradas)]


# ------------------------------------------------------------------ sobre el árbol real


def test_el_censo_estatico_encuentra_rutas() -> None:
    assert len(_todas_las_rutas()) > 300, "el patrón del decorador dejó de casar: los guardianes vigilarían el vacío"


def test_cada_exclusion_casa_con_una_ruta_real() -> None:
    entradas = _censo().EXCLUIDAS_A_PROPOSITO
    assert len(entradas) > 30
    huerfanas = _sin_ruta(entradas, _todas_las_rutas())
    assert not huerfanas, (
        "Estas exclusiones no casan con ninguna ruta de los routers: o la ruta cambió (actualiza el "
        "patrón) o la decisión caducó (quítala, y el censo volverá a contar el hueco):\n  " + "\n  ".join(huerfanas)
    )


def test_las_exclusiones_selladas_solo_casan_con_rutas_selladas() -> None:
    entradas = _censo().EXCLUIDAS_A_PROPOSITO
    assert any(e[3].startswith("SELLADA: ") for e in entradas)
    malas = _selladas_sin_sello(entradas, _todas_las_rutas())
    assert not malas, (
        "Declaradas como selladas con X-Internal-Token y el router no lo exige (una persona podría "
        "alcanzarlas, y entonces son un hueco o una fuga, no una exclusión):\n  " + "\n  ".join(malas)
    )


def test_las_referencias_a_no_ofrecidas_y_a_ficheros_existen() -> None:
    entradas = _censo().EXCLUIDAS_A_PROPOSITO
    rotas = _referencias_rotas(entradas, _no_ofrecidas())
    assert not rotas, "Referencias que no existen:\n  " + "\n  ".join(rotas)


def test_toda_no_ofrecida_tiene_exclusion_o_razon_de_no_tenerla() -> None:
    censo = _censo()
    operaciones = _no_ofrecidas()
    sin_ruta: dict[str, str] = censo.NO_OFRECIDAS_SIN_RUTA
    faltan = _no_representadas(censo.EXCLUIDAS_A_PROPOSITO, operaciones, sin_ruta)
    assert not faltan, (
        "El asistente declara estos actos como no ofrecidos y el censo los contaría como hueco real:\n  "
        + "\n  ".join(faltan)
    )
    inexistentes = [o for o in sin_ruta if not any(x.startswith(o) for x in operaciones)]
    assert not inexistentes, f"NO_OFRECIDAS_SIN_RUTA cita actos que NO_OFRECIDAS no declara: {inexistentes}"
    assert all(len(motivo) > 20 for motivo in sin_ruta.values())


def test_ninguna_ruta_excluida_tiene_herramienta() -> None:
    censo = _censo()
    contradichas = _excluidas_con_herramienta(censo.EXCLUIDAS_A_PROPOSITO, censo.cubiertas())
    assert not contradichas, (
        "Declaradas «excluidas a propósito» y el catálogo MCP las ofrece: una de las dos afirmaciones es "
        "falsa. Quita la exclusión o la herramienta:\n  " + "\n  ".join(contradichas)
    )


def test_la_lista_no_se_repite_ni_deja_motivos_vacios() -> None:
    entradas = _censo().EXCLUIDAS_A_PROPOSITO
    claves = [(e[0], e[1]) for e in entradas]
    assert len(claves) == len(set(claves)), "hay exclusiones duplicadas"
    assert all(len(e[2]) > 15 and e[3] for e in entradas), "toda exclusión lleva motivo y referencia"


# ------------------------------------------------------------------ controles positivos

_ROUTER_SINTETICO = '''
router = APIRouter(prefix="/api/v1/x")
sellado = APIRouter(prefix="/api/v1/s", dependencies=[Depends(require_internal_token)])


@router.post("/abierta/{id}")
async def uno() -> None: ...


@router.get("/sellada", dependencies=[Depends(require_internal_token)])
async def dos() -> None: ...


@sellado.get("/heredada")
async def tres() -> None: ...
'''


def test_control_el_lector_de_rutas_ve_prefijo_y_sello() -> None:
    rutas = {(m, r): n for m, r, n in _rutas_de(_ROUTER_SINTETICO)}
    assert set(rutas) == {("POST", "/api/v1/x/abierta/{id}"), ("GET", "/api/v1/x/sellada"), ("GET", "/api/v1/s/heredada")}
    assert not rutas[("POST", "/api/v1/x/abierta/{id}")] & _SELLOS
    assert rutas[("GET", "/api/v1/x/sellada")] & _SELLOS
    assert rutas[("GET", "/api/v1/s/heredada")] & _SELLOS


def test_control_una_exclusion_sin_ruta_se_acusa() -> None:
    rutas = _rutas_de(_ROUTER_SINTETICO)
    buena: Entrada = ("POST", r"^/api/v1/x/abierta/\{[^/]+\}$", "motivo suficiente aquí", "NO_OFRECIDAS: A")
    caducada: Entrada = ("POST", r"^/api/v1/x/borrada$", "motivo suficiente aquí", "NO_OFRECIDAS: A")
    equivocada_de_metodo: Entrada = ("GET", r"^/api/v1/x/abierta/\{[^/]+\}$", "motivo suficiente aquí", "NO_OFRECIDAS: A")
    assert _sin_ruta((buena,), rutas) == []
    assert _sin_ruta((caducada, equivocada_de_metodo), rutas) == [caducada[1], equivocada_de_metodo[1]]


def test_control_una_sellada_que_una_persona_alcanza_se_acusa() -> None:
    rutas = _rutas_de(_ROUTER_SINTETICO)
    mentirosa: Entrada = ("POST", r"^/api/v1/x/abierta/\{[^/]+\}$", "motivo suficiente aquí", "SELLADA: scripts/censo_mcp.py")
    honesta: Entrada = ("GET", r"^/api/v1/(x/sellada|s/heredada)$", "motivo suficiente aquí", "SELLADA: scripts/censo_mcp.py")
    assert _selladas_sin_sello((mentirosa,), rutas) == ["POST /api/v1/x/abierta/{id}"]
    assert _selladas_sin_sello((honesta,), rutas) == []


def test_control_una_referencia_inexistente_se_acusa() -> None:
    operaciones = ["Ejecutar el borrado de un lote de eliminación"]
    ok: Entrada = ("POST", "^/a$", "motivo suficiente aquí", "NO_OFRECIDAS: Ejecutar el borrado")
    sin_acto: Entrada = ("POST", "^/a$", "motivo suficiente aquí", "NO_OFRECIDAS: Acto inventado")
    sin_fichero: Entrada = ("POST", "^/a$", "motivo suficiente aquí", "docs/es/no-existe.md (F1)")
    assert _referencias_rotas((ok,), operaciones) == []
    assert _referencias_rotas((sin_acto, sin_fichero), operaciones) == [sin_acto[3], sin_fichero[3]]


def test_control_un_acto_declarado_sin_exclusion_se_acusa() -> None:
    operaciones = ["Ejecutar el borrado de un lote", "Trabajar un buzón PERSONAL", "Acto huérfano"]
    e: Entrada = ("POST", "^/a$", "motivo suficiente aquí", "NO_OFRECIDAS: Ejecutar el borrado")
    assert _no_representadas((e,), operaciones, {"Trabajar un buzón PERSONAL": "razón"}) == ["Acto huérfano"]


def test_control_una_exclusion_con_herramienta_se_acusa() -> None:
    e: Entrada = ("POST", r"^/api/v1/eliminacion/lotes/\{[^/]+\}/ejecutar$", "motivo suficiente aquí", "NO_OFRECIDAS: x")
    catalogo = {("POST", "/api/v1/eliminacion/lotes/{x}/ejecutar"), ("GET", "/api/v1/eliminacion/lotes")}
    assert _excluidas_con_herramienta((e,), catalogo) == ["POST /api/v1/eliminacion/lotes/{x}/ejecutar"]
    assert _excluidas_con_herramienta((e,), {("GET", "/api/v1/eliminacion/lotes")}) == []
