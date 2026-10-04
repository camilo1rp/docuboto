# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El nivel CRUD que el catálogo MCP declara para la eliminación casa con el backend (T6, AREA-06-01).

Subir `avalar` y `ejecutar` a `USUA_PERM_TRD crud>=4` en archive-service dejó al
catálogo MCP con `min_crud=3` (lo corrigió el implementador a mano): sin este
guardián, la próxima vez que un lado cambie el otro ofrece una tool que el
backend rechaza con 403 (nivel menor), o esconde una que sí podría usar (mayor).
Ambos lados se DERIVAN del código (AST), no se copian.

Además, la destrucción NO tiene tool MCP: `ejecutar` se hace desde la interfaz,
con sesión propia y por quien no avaló (separación de funciones). Un `Tool` con
la ruta `/ejecutar` rompería esa decisión sin que ningún test del servicio lo viera.
"""

from __future__ import annotations

import ast
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_CATALOGO = _RAIZ / "services" / "mcp-server" / "app" / "tools" / "catalog.py"
_ROUTER = _RAIZ / "services" / "archive-service" / "app" / "routers" / "eliminacion.py"
_PREFIJO = "/api/v1/eliminacion"


def _min_crud_de(call: ast.Call) -> int | None:
    """`min_crud` del `require_permission(...)` dentro de `dependencies=[...]`."""
    for kw in call.keywords:
        if kw.arg != "dependencies":
            continue
        for nodo in ast.walk(kw.value):
            if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") == "require_permission":
                for k in nodo.keywords:
                    if k.arg == "min_crud" and isinstance(k.value, ast.Constant):
                        return int(k.value.value)
                return 1  # default de require_permission
    return None


def _rutas_del_backend() -> dict[tuple[str, str], int | None]:
    salida: dict[tuple[str, str], int | None] = {}
    for nodo in ast.walk(ast.parse(_ROUTER.read_text(encoding="utf-8"))):
        if not (isinstance(nodo, ast.AsyncFunctionDef | ast.FunctionDef)):
            continue
        for dec in nodo.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in {"get", "post", "put", "patch", "delete"}
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                salida[(dec.func.attr.upper(), _PREFIJO + str(dec.args[0].value))] = _min_crud_de(dec)
    return salida


def _tools_del_catalogo() -> list[dict]:
    tools = []
    for nodo in ast.walk(ast.parse(_CATALOGO.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") == "Tool":
            campos = {
                kw.arg: kw.value.value
                for kw in nodo.keywords
                if kw.arg and isinstance(kw.value, ast.Constant)
            }
            if str(campos.get("path", "")).startswith(_PREFIJO):
                tools.append(campos)
    return tools


def test_el_min_crud_de_cada_tool_de_eliminacion_es_el_de_su_ruta_en_el_backend() -> None:
    backend = _rutas_del_backend()
    tools = _tools_del_catalogo()
    comparadas = 0
    desajustes = []
    for t in tools:
        ruta = backend.get((str(t["method"]), str(t["path"])))
        assert (str(t["method"]), str(t["path"])) in backend, (
            f"{t['name']} apunta a {t['method']} {t['path']}, que archive-service no declara"
        )
        comparadas += 1
        if int(t.get("min_crud", 1)) != ruta:
            desajustes.append((t["name"], t.get("min_crud", 1), ruta))
    assert not desajustes, f"(tool, catálogo, backend): {desajustes}"
    # Control positivo: sin él, un AST que no encuentra nada pasa en vacío.
    assert comparadas >= 4
    avalar = [t for t in tools if t["name"] == "avalar_lote_eliminacion"]
    assert avalar and int(avalar[0]["min_crud"]) == 4


def test_la_destruccion_no_tiene_tool_mcp() -> None:
    assert not [t["name"] for t in _tools_del_catalogo() if str(t["path"]).endswith("/ejecutar")]
    # Y el control positivo: la ruta existe en el backend, así que la ausencia es decisión.
    assert ("POST", f"{_PREFIJO}/lotes/{{lote_id}}/ejecutar") in _rutas_del_backend()
