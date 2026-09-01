# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Lo que el catálogo MCP declara sobre un endpoint tiene que ser lo que el endpoint
exige.

**Las dos formas de mentir, y las dos duelen.**

- *Exigir de más* —o inventarse un permiso— **esconde** la tool a quien sí puede
  usarla. Así estuvo `radicar_documento` desde su primer commit, declarando
  `RADI_CREAR`, un permiso que no existe en el sistema: la única escritura del
  MCP era invisible para todo usuario no-ROOT.
- *Exigir de menos* la **ofrece a quien va a recibir un 403**. Así estaban
  `consultar_expediente`, `indice_expediente` y `consultar_fuid`, declarando
  «sin permiso» sobre endpoints gateados con `USUA_PERM_EXPEDIENTE`.

El segundo es el que este proyecto se comprometió a no cometer: el marco de
alcance existe precisamente para que nadie descubra un límite fallando. Un
catálogo que promete lo que el backend niega convierte ese marco en una lista de
buenas intenciones.

`test_catalog_invariants.py` (en mcp-server) ya comprueba que el permiso EXISTA
en las migraciones de RBAC. Eso habría cazado `RADI_CREAR` y no habría cazado
estos tres: un `None` es un nombre perfectamente existente. Lo que falta —y es lo
que hace este archivo— es comparar contra **el gate del endpoint concreto**, y
para eso hacen falta los dos árboles a la vez, que solo se ven desde `shared/`.

Ambos lados se derivan del código: el catálogo por sus `Tool(...)` y los gates
por los `require_permission(...)` de los decoradores. No hay tercera lista.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_CATALOGO = _RAIZ / "services" / "mcp-server" / "app" / "tools" / "catalog.py"
_SERVICIOS = _RAIZ / "services"

_PARAM = re.compile(r"\{[^}]+\}")


def _normalizar(ruta: str) -> str:
    """`/documents/{document_id}` y `/documents/{id}` son la misma ruta.

    El catálogo nombra sus parámetros para la persona («document_id») y el router
    para el handler («document_id», «id», «expediente_id»…). Comparar los nombres
    produciría falsos negativos justo donde el test tiene que ser tajante.
    """
    ruta = _PARAM.sub("{}", ruta.rstrip("/"))
    # Los convertidores de Starlette (`{tracking_number:path}`) ya caen con el
    # patrón de arriba, pero la barra final no: `/trd` y `/trd/` son la misma.
    return ruta or "/"


def _routers_del_fichero(arbol: ast.AST) -> dict[str, tuple[str, tuple[str | None, int]]]:
    """Variable del router → (prefijo, gate del propio router).

    Antes se guardaba UN prefijo por fichero, el del último `APIRouter(...)` que
    apareciera. Un fichero con dos routers —`anulacion.py` tiene el de
    `/api/v1/documents` y el de la cola `/api/v1/anulaciones`— dejaba a los
    endpoints del primero colgando de un prefijo ajeno, así que **no casaban con
    ninguna tool y el cruce los daba por inexistentes**: cuatro tools quedaron
    sin comprobar y el guardián no lo dijo hasta que el recuento total bajó.

    Y se lee el gate del PROPIO router (`APIRouter(..., dependencies=[...])`),
    que antes se ignoraba por completo. Ese olvido tenía las dos caras: acusaba
    de «exigir de más» a una tool que declaraba el permiso correcto, y —lo
    grave— **no habría cazado a una tool que declarara de MENOS** frente a un
    gate de router, que es el caso que este guardián existe para impedir.
    """
    routers: dict[str, tuple[str, tuple[str | None, int]]] = {}
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Assign) or len(nodo.targets) != 1:
            continue
        destino = nodo.targets[0]
        if not isinstance(destino, ast.Name):
            continue
        llamada = nodo.value
        if not (isinstance(llamada, ast.Call) and getattr(llamada.func, "id", "") == "APIRouter"):
            continue
        prefijo = ""
        for kw in llamada.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefijo = str(kw.value.value)
        routers[destino.id] = (prefijo, _leer_gate(llamada))
    return routers


def _gates_de_los_routers() -> dict[tuple[str, str], tuple[str | None, int]]:
    """(método, ruta normalizada) → (permiso, min_crud) efectivo.

    Efectivo = el del decorador si lo declara; si no, el del router que lo
    contiene. Un endpoint sin ninguno queda como `(None, 1)`: no es un vacío de
    información, es la declaración de que no exige permiso atómico.
    """
    gates: dict[tuple[str, str], tuple[str | None, int]] = {}
    for fichero in sorted(_SERVICIOS.glob("*/app/routers/*.py")):
        try:
            arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — un router roto lo caza su lint
            continue

        routers = _routers_del_fichero(arbol)

        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for deco in nodo.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                attr = getattr(deco.func, "attr", "")
                if attr not in ("get", "post", "patch", "put", "delete"):
                    continue
                if not (deco.args and isinstance(deco.args[0], ast.Constant)):
                    continue
                variable = getattr(deco.func.value, "id", "")
                prefijo, gate_router = routers.get(variable, ("", (None, 1)))
                ruta = _normalizar(prefijo + str(deco.args[0].value))
                propio = _leer_gate(deco)
                gates[(attr.upper(), ruta)] = propio if propio[0] else gate_router
    return gates


def _leer_gate(deco: ast.Call) -> tuple[str | None, int]:
    for kw in deco.keywords:
        if kw.arg != "dependencies" or not isinstance(kw.value, ast.List):
            continue
        for elemento in kw.value.elts:
            for sub in ast.walk(elemento):
                if not (
                    isinstance(sub, ast.Call)
                    and getattr(sub.func, "id", "") == "require_permission"
                ):
                    continue
                perm = (
                    str(sub.args[0].value)
                    if sub.args and isinstance(sub.args[0], ast.Constant)
                    else None
                )
                crud = 1
                for k in sub.keywords:
                    if k.arg == "min_crud" and isinstance(k.value, ast.Constant):
                        crud = int(k.value.value)
                return perm, crud
    return None, 1


def _tools() -> list[dict]:
    arbol = ast.parse(_CATALOGO.read_text(encoding="utf-8"))
    salida = []
    for nodo in ast.walk(arbol):
        if not (isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") == "Tool"):
            continue
        campos = {
            kw.arg: kw.value.value
            for kw in nodo.keywords
            if isinstance(kw.value, ast.Constant) and kw.arg
        }
        if "path" in campos and "method" in campos:
            salida.append(campos)
    return salida


def test_ninguna_tool_declara_menos_permiso_del_que_el_endpoint_exige() -> None:
    """El caso que ofrece la tool a quien recibirá un 403."""
    gates = _gates_de_los_routers()
    assert gates, "no se extrajo ningún gate: el parseo de los routers se rompió"

    mentiras = []
    for t in _tools():
        clave = (str(t["method"]), _normalizar(str(t["path"])))
        if clave not in gates:
            continue  # cobertura del cruce: la fija el test de abajo
        perm_real, crud_real = gates[clave]
        if perm_real is None:
            continue
        if t.get("permission") != perm_real or int(t.get("min_crud", 1)) < crud_real:
            mentiras.append(
                {
                    "tool": t.get("name"),
                    "declara": (t.get("permission"), t.get("min_crud", 1)),
                    "exige": (perm_real, crud_real),
                }
            )

    assert not mentiras, (
        "estas tools se ofrecerían a quien no puede ejecutarlas (403 al confirmar): "
        f"{mentiras}"
    )


def test_ninguna_tool_exige_un_permiso_que_el_endpoint_no_pide() -> None:
    """El caso que ESCONDE la tool a quien sí puede — el de `RADI_CREAR`.

    Se comprueba aparte del anterior a propósito: son dos fallos distintos, con
    dos víctimas distintas, y un test que los mezclara no diría cuál ocurrió.
    """
    gates = _gates_de_los_routers()
    excesos = []
    for t in _tools():
        clave = (str(t["method"]), _normalizar(str(t["path"])))
        if clave not in gates:
            continue
        perm_real, _crud_real = gates[clave]
        declarado = t.get("permission")
        if declarado is None or perm_real is not None:
            continue
        # Un `min_crud` por encima del del endpoint puede ser deliberado cuando el
        # endpoint alberga varios actos con umbrales distintos —
        # `tipificar_radicado` es el caso: el PATCH está a 1 y tipificar exige 3,
        # comprobado dentro del handler— así que aquí solo se persigue el permiso.
        if t.get("permiso_en_runtime"):
            # Excepción DECLARADA en el catálogo: el servicio resuelve el permiso
            # en tiempo de ejecución y no hay decorador que leer. Se admite, y el
            # test de abajo comprueba que la marca no se use para tapar un gate
            # que sí existe.
            continue
        excesos.append({"tool": t.get("name"), "declara": declarado, "exige": None})

    assert not excesos, (
        "estas tools exigen un permiso que su endpoint no pide: quedarían "
        f"invisibles para quien sí puede usarlas: {excesos}"
    )


def test_la_marca_de_permiso_en_runtime_no_tapa_un_gate_existente() -> None:
    """La excepción no puede convertirse en interruptor.

    `permiso_en_runtime=True` le dice al test de arriba «aquí no hay decorador
    que comparar». Si se pusiera sobre una ruta que SÍ tiene `require_permission`,
    dejaría de compararse un caso perfectamente comparable — y esa es la forma
    en que una excepción legítima se convierte, con el tiempo, en la manera de
    callar hallazgos.
    """
    gates = _gates_de_los_routers()
    tapadas = []
    for t in _tools():
        if not t.get("permiso_en_runtime"):
            continue
        clave = (str(t["method"]), _normalizar(str(t["path"])))
        perm_real = gates.get(clave, (None, 1))[0]
        if perm_real is not None:
            tapadas.append({"tool": t.get("name"), "gate_real": perm_real})

    assert not tapadas, (
        "estas tools se marcan como de permiso en runtime y su ruta SÍ tiene "
        f"gate de decorador: la marca sobra y oculta la comparación: {tapadas}"
    )


def test_el_cruce_alcanza_a_la_mayoria_del_catalogo() -> None:
    """Control de cobertura, sin el cual los dos tests de arriba son decorativos.

    Si el parseo dejara de casar rutas —un prefijo compuesto, un decorador con la
    ruta en una variable—, `clave not in gates` haría que TODO se saltara y los
    dos tests quedarían verdes para siempre sin comprobar nada. Es el modo de
    fallo de los tests que filtran antes de afirmar, y por eso se mide.
    """
    gates = _gates_de_los_routers()
    tools = _tools()
    casadas = [t for t in tools if (str(t["method"]), _normalizar(str(t["path"]))) in gates]

    assert len(casadas) >= len(tools) - 1, (
        f"solo se cruzaron {len(casadas)} de {len(tools)} tools con su endpoint; "
        "el parseo de rutas dejó de casar y los tests de permisos no están "
        f"comprobando nada. Sin casar: "
        f"{[t.get('name') for t in tools if t not in casadas]}"
    )
