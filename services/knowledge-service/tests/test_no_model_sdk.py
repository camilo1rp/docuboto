# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Guardián de ADR-046: knowledge-service no llama a modelos.

Falla si algún módulo de `app/` importa un SDK de modelos o lee una variable de
proveedor (`AI_*`, `EMBEDDING_*`, `OLLAMA_*`, `ANTHROPIC_*`), sea por `os.environ` o
como campo de `Settings` (pydantic la lee por su nombre en mayúsculas).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

MODEL_SDKS = {"anthropic", "openai", "sentence_transformers", "ollama", "torch", "transformers"}
PROVIDER_VAR = re.compile(r"^(AI|EMBEDDING|OLLAMA|ANTHROPIC)_", re.IGNORECASE)


def _violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in MODEL_SDKS]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in MODEL_SDKS:
                found.append(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if PROVIDER_VAR.match(node.value):
                found.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if PROVIDER_VAR.match(node.target.id):
                found.append(node.target.id)
    return found


def test_no_module_imports_a_model_sdk_or_reads_provider_vars() -> None:
    files = sorted(APP.rglob("*.py"))
    assert files, "el censo de módulos de app/ se vació"
    offenders = {
        str(f.relative_to(APP)): v for f in files if (v := _violations(f.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_positive_control_detects_each_form() -> None:
    source = (
        "import anthropic\n"
        "from sentence_transformers import SentenceTransformer\n"
        "import os\n"
        "x = os.environ['OLLAMA_URL']\n"
        "class S:\n"
        "    ai_provider: str = 'x'\n"
    )
    assert set(_violations(source)) == {"anthropic", "sentence_transformers", "OLLAMA_URL", "ai_provider"}
