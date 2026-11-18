# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cross-package calls go through `<paquete>.api` (ADR-047 §1, §4).

Without a network boundary, a call between domains is a function call. It still
has one door: a module may import another package's `api` and nothing else from
that package, so routers, services and repositories stay private to their
domain. The composition root (`docuboto`) may import anything. Tests are out
of scope.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._compose import RAIZ, glob_src

SERVICIOS = RAIZ / "services"

#: Domain packages. Any of them may reach another one, only through `<pkg>.api`.
DOMINIOS = frozenset(
    {"archive", "auth", "document", "knowledge", "notification", "signature", "storage", "tenant", "workflow"}
)

_RAIZ = frozenset({"docuboto"})


def _imports(arbol: ast.AST) -> list[str]:
    out: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            out.extend(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            out.append(nodo.module)
    return out


def _paquete_de(ruta: Path) -> str | None:
    partes = ruta.relative_to(SERVICIOS).parts
    if len(partes) < 2:
        return None
    return partes[1]


def test_los_cruces_entre_dominios_pasan_por_api() -> None:
    culpables: list[str] = []
    for ruta in glob_src(SERVICIOS, "*/app/**/*.py"):
        if "tests" in ruta.parts:
            continue
        origen = _paquete_de(ruta)
        if origen is None or origen in _RAIZ:
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        for modulo in _imports(arbol):
            raiz = modulo.split(".", 1)[0]
            if raiz not in DOMINIOS or raiz == origen:
                continue
            if modulo != f"{raiz}.api" and not modulo.startswith(f"{raiz}.api."):
                culpables.append(f"{ruta.relative_to(RAIZ)} importa {modulo}")
    assert not culpables, (
        "Un paquete de dominio solo importa el `api` de otro:\n  " + "\n  ".join(culpables)
    )


def test_el_censo_ve_el_primer_cruce() -> None:
    """Anti-vacuidad: sin el import, el test de arriba pasaría sobre cero cruces."""
    repo = (
        SERVICIOS / "docuboto" / "workflow" / "repositories" / "workflow_repository.py"
    )
    texto = repo.read_text(encoding="utf-8")
    assert "from document.api import mover_flujo" in texto


def test_document_no_importa_workflow() -> None:
    """Registration talks to workflow through the composition-root hook, not an import."""
    culpables: list[str] = []
    for ruta in glob_src(SERVICIOS, "*/app/**/*.py"):
        if "tests" in ruta.parts:
            continue
        partes = ruta.relative_to(SERVICIOS).parts
        if len(partes) < 2 or partes[1] != "document":
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        for modulo in _imports(arbol):
            if modulo == "workflow" or modulo.startswith("workflow."):
                culpables.append(f"{ruta.relative_to(RAIZ)} importa {modulo}")
    assert not culpables, (
        "document no importa workflow: el lifespan registra el hook:\n  "
        + "\n  ".join(culpables)
    )
