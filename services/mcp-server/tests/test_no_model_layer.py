# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Fase D0 (ADR-046 §6): mcp-server no llama a ningún modelo.

El asistente conversacional era lo último de Docuboto que hablaba con un modelo, y
se retiró. Este guardián impide que vuelva por la puerta de atrás: falla si algún
módulo de `app/` importa un SDK de modelos o lee una variable de entorno de la
capa de modelos (`AI_*`, `EMBEDDING_*`, `OLLAMA_*`, `ANTHROPIC_*`).

Las lecturas de entorno se buscan por las tres vías que hay en el código:
`os.getenv(...)`, `os.environ[...]` / `os.environ.get(...)`, y los campos de una
clase `BaseSettings`, cuyo nombre en minúsculas ES la variable que pydantic lee.

El análisis es por AST y no por texto: un comentario que nombre `anthropic` (como
este) no es una dependencia.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN_SDKS = frozenset({"anthropic", "openai", "sentence_transformers", "ollama"})
FORBIDDEN_ENV_PREFIXES = ("AI_", "EMBEDDING_", "OLLAMA_", "ANTHROPIC_")


def _forbidden_env(nombre: str) -> bool:
    return nombre.upper().startswith(FORBIDDEN_ENV_PREFIXES)


def _imports(arbol: ast.AST) -> set[str]:
    """Paquete raíz de cada `import x` y `from x import y` del módulo."""
    raices: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            raices |= {alias.name.split(".")[0] for alias in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            raices.add(nodo.module.split(".")[0])
    # `importlib.import_module("anthropic")` también es un import.
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and _nombre_llamada(nodo.func) in {"import_module", "__import__"}
            and nodo.args
            and isinstance(nodo.args[0], ast.Constant)
            and isinstance(nodo.args[0].value, str)
        ):
            raices.add(nodo.args[0].value.split(".")[0])
    return raices


def _nombre_llamada(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _es_environ(nodo: ast.expr) -> bool:
    return (isinstance(nodo, ast.Attribute) and nodo.attr == "environ") or (
        isinstance(nodo, ast.Name) and nodo.id == "environ"
    )


def _env_leidas(arbol: ast.AST) -> set[str]:
    """Nombres de variables de entorno que el módulo lee, por cualquiera de las vías."""
    leidas: set[str] = set()
    for nodo in ast.walk(arbol):
        # os.getenv("X") / os.environ.get("X")
        if isinstance(nodo, ast.Call) and nodo.args and isinstance(nodo.args[0], ast.Constant):
            func = nodo.func
            es_getenv = _nombre_llamada(func) == "getenv"
            es_environ_get = (
                isinstance(func, ast.Attribute) and func.attr == "get" and _es_environ(func.value)
            )
            if (es_getenv or es_environ_get) and isinstance(nodo.args[0].value, str):
                leidas.add(nodo.args[0].value)
        # os.environ["X"]
        elif (
            isinstance(nodo, ast.Subscript)
            and _es_environ(nodo.value)
            and isinstance(nodo.slice, ast.Constant)
            and isinstance(nodo.slice.value, str)
        ):
            leidas.add(nodo.slice.value)
        # class Settings(BaseSettings): ai_provider: str = ...
        elif isinstance(nodo, ast.ClassDef) and any(
            _nombre_llamada(base) == "BaseSettings" for base in nodo.bases
        ):
            for campo in nodo.body:
                if isinstance(campo, ast.AnnAssign) and isinstance(campo.target, ast.Name):
                    leidas.add(campo.target.id)
    return leidas


def _modulos() -> list[Path]:
    return sorted(_APP.rglob("*.py"))


def _ofensores(detector) -> list[str]:  # noqa: ANN001
    hallados: list[str] = []
    for archivo in _modulos():
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nombre in sorted(detector(arbol)):
            hallados.append(f"{archivo.relative_to(_APP.parent)}: {nombre}")
    return hallados


def test_no_module_imports_a_model_sdk() -> None:
    ofensores = _ofensores(lambda a: _imports(a) & FORBIDDEN_SDKS)
    assert not ofensores, (
        "mcp-server no llama a modelos (ADR-046 §6). Quien quiera IA usa un cliente "
        f"MCP externo o las sugerencias del proveedor: {ofensores}"
    )


def test_no_module_reads_model_env_vars() -> None:
    ofensores = _ofensores(lambda a: {n for n in _env_leidas(a) if _forbidden_env(n)})
    assert not ofensores, (
        f"mcp-server no lee configuración de la capa de modelos (ADR-046 §6): {ofensores}"
    )


def test_census_sees_the_app_modules() -> None:
    """Anti-vacuidad: los guardianes pasarían igual si el glob no hallara nada."""
    nombres = {p.name for p in _modulos()}
    assert {"main.py", "config.py", "catalog.py"} <= nombres
    config = ast.parse((_APP / "core" / "config.py").read_text(encoding="utf-8"))
    assert "mcp_proposal_secret" in _env_leidas(config), "no se leyeron los campos de Settings"


def test_detector_catches_synthetic_offenders() -> None:
    """Control positivo: cada vía de entrada que el guardián dice cubrir, cubierta."""
    fuente = '''
import anthropic
from openai import OpenAI
import sentence_transformers.util
from ollama import Client
import importlib
importlib.import_module("anthropic")
import os
from os import environ
from pydantic_settings import BaseSettings

os.getenv("AI_PROVIDER")
os.environ.get("EMBEDDING_PROVIDER")
os.environ["OLLAMA_BASE_URL"]
environ.get("ANTHROPIC_API_KEY")

class Settings(BaseSettings):
    ai_model: str = ""
    gateway_url: str = ""
'''
    arbol = ast.parse(fuente)

    assert FORBIDDEN_SDKS <= _imports(arbol)
    prohibidas = {n for n in _env_leidas(arbol) if _forbidden_env(n)}
    assert prohibidas == {
        "AI_PROVIDER",
        "EMBEDDING_PROVIDER",
        "OLLAMA_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ai_model",
    }
    assert "gateway_url" in _env_leidas(arbol)
