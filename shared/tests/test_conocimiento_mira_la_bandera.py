# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Toda ruta que consulta la capa de conocimiento mira la bandera de la entidad
(ADR-037). Es una frontera por ADMISIÓN: una ruta nueva sin el chequeo rompe
este test, en vez de quedar abierta en silencio.

Dos comprobaciones, derivadas del árbol (no hay lista a mano):

1. Toda ruta de ``knowledge-service/app/routers/knowledge.py`` declara en su decorador
   ``require_asistente_habilitado`` y ``require_permission("PERM_CONOCIMIENTO")``.
2. Todo llamante de ``visible_tools`` / ``procedimientos_visibles`` / ``puede_usar`` /
   ``authorize`` / ``procedure_runner.ejecutar`` en ``mcp-server/app`` pasa
   ``asistente_habilitado=`` explícito: el default es ``False`` (fail-closed), así que
   olvidarlo no abre nada, pero esconde las tools de conocimiento a quien sí puede verlas.

Cada comprobación trae su control positivo sobre un fuente sintético.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
KNOWLEDGE = RAIZ / "services" / "knowledge-service" / "app" / "routers" / "knowledge.py"
MCP_APP = RAIZ / "services" / "mcp-server" / "app"

_METODOS = {"get", "post", "put", "patch", "delete"}
_NECESITAN_BANDERA = {
    "visible_tools",
    "procedimientos_visibles",
    "puede_usar",
    "authorize",
    "_tools_del_asistente",
}


def _nombres(nodo: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(nodo) if isinstance(n, ast.Name)}


def _llamadas(nodo: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(nodo) if isinstance(n, ast.Call)]


def _nombre_llamado(c: ast.Call) -> str:
    f = c.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _rutas(fuente: str) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    out = []
    for n in ast.walk(ast.parse(fuente)):
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef):
            for d in n.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in _METODOS
                    and isinstance(d.func.value, ast.Name)
                    and d.func.value.id == "router"
                ):
                    out.append(n)
    return out


def _decoradores(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> ast.AST:
    return ast.Module(body=[ast.Expr(d) for d in fn.decorator_list], type_ignores=[])


def sin_bandera_knowledge(fuente: str) -> list[str]:
    """Rutas de knowledge sin ``require_asistente_habilitado`` en el decorador."""
    return [f.name for f in _rutas(fuente) if "require_asistente_habilitado" not in _nombres(_decoradores(f))]


def sin_permiso_knowledge(fuente: str) -> list[str]:
    """Rutas de knowledge sin ``require_permission``."""
    return [f.name for f in _rutas(fuente) if "require_permission" not in _nombres(_decoradores(f))]


def llamantes_sin_bandera(fuente: str) -> list[str]:
    """Llamadas a las funciones sensibles a la bandera que no la pasan por nombre."""
    arbol = ast.parse(fuente)
    malas = []
    for c in _llamadas(arbol):
        nombre = _nombre_llamado(c)
        es_runner = (
            nombre == "ejecutar"
            and isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "procedure_runner"
        )
        if (nombre in _NECESITAN_BANDERA or es_runner) and not any(
            k.arg == "asistente_habilitado" for k in c.keywords
        ):
            malas.append(f"{nombre} (línea {c.lineno})")
    return malas


# --- 1. knowledge -------------------------------------------------------------------


def test_toda_ruta_de_knowledge_mira_la_bandera_y_las_de_consulta_el_permiso() -> None:
    fuente = KNOWLEDGE.read_text()
    assert len(_rutas(fuente)) >= 4, "el censo de rutas de knowledge se vació"
    assert sin_bandera_knowledge(fuente) == []
    assert sin_permiso_knowledge(fuente) == []


def test_ninguna_otra_ruta_de_consulta_de_knowledge_queda_fuera() -> None:
    """Un router nuevo de consulta en el servicio tiene que decidir su postura aquí."""
    routers = {p.name for p in KNOWLEDGE.parent.glob("*.py")} - {"__init__.py"}
    assert routers == {"knowledge.py", "health.py"}, (
        f"routers nuevos en knowledge-service: {sorted(routers)}. Si consultan la base de "
        "conocimiento deben llevar require_asistente_habilitado + PERM_CONOCIMIENTO; añádelo "
        "a las comprobaciones de este archivo."
    )


def test_control_positivo_knowledge_detecta_la_ruta_sin_gate() -> None:
    sintetico = '''
router = APIRouter()

@router.post("/ok", dependencies=[Depends(require_asistente_habilitado), Depends(require_permission("PERM_CONOCIMIENTO"))])
async def ok(): ...

@router.post("/sin_bandera", dependencies=[Depends(require_permission("PERM_CONOCIMIENTO"))])
async def sin_bandera(): ...

@router.post("/sin_permiso", dependencies=[Depends(require_asistente_habilitado)])
async def sin_permiso(): ...
'''
    assert sin_bandera_knowledge(sintetico) == ["sin_bandera"]
    assert sin_permiso_knowledge(sintetico) == ["sin_permiso"]


# --- 2. los llamantes pasan la bandera ------------------------------------------------------


def test_todo_llamante_del_catalogo_pasa_la_bandera() -> None:
    malos: list[str] = []
    for ruta in sorted(MCP_APP.rglob("*.py")):
        for m in llamantes_sin_bandera(ruta.read_text()):
            malos.append(f"{ruta.relative_to(RAIZ)}: {m}")
    assert malos == [], "llamadas que no pasan asistente_habilitado=:\n  " + "\n  ".join(malos)


def test_control_positivo_llamante_sin_bandera() -> None:
    sintetico = '''
def f(identidad):
    visible_tools(identidad.permissions, is_root=False)
    visible_tools(identidad.permissions, asistente_habilitado=identidad.asistente_habilitado)
    procedure_runner.ejecutar(client, proc, args)
    write_gate.ejecutar(client, token)
'''
    assert [m.split(" ")[0] for m in llamantes_sin_bandera(sintetico)] == ["visible_tools", "ejecutar"]
