# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Solo el punto de paso importa el cliente PPI (ADR-045 §3)."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PERMISO = Path("core") / "paso.py"


def _usa_el_cliente(fuente: str) -> list[str]:
    hallazgos: list[str] = []
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.ImportFrom) and nodo.module:
            if nodo.module == "app.clients.ppi" or nodo.module.endswith(".ppi"):
                hallazgos.append(nodo.module)
            if any(alias.name in {"ppi", "ClientePpi"} for alias in nodo.names):
                if nodo.module == "app.clients" or nodo.module.endswith(".clients"):
                    hallazgos.append(nodo.module)
        elif isinstance(nodo, ast.Import):
            for alias in nodo.names:
                if alias.name == "app.clients.ppi" or alias.name.endswith(".ppi"):
                    hallazgos.append(alias.name)
        elif isinstance(nodo, ast.Constant) and nodo.value == "app.clients.ppi":
            hallazgos.append(nodo.value)
        elif isinstance(nodo, ast.Name) and nodo.id in {"ClientePpi", "ppi"}:
            hallazgos.append(nodo.id)
    return hallazgos


def test_ningun_otro_modulo_usa_el_cliente() -> None:
    archivos = sorted(APP.rglob("*.py"))
    assert len(archivos) >= 8, "el censo de app/ se vació"
    culpables = {
        str(ruta.relative_to(APP)): hallados
        for ruta in archivos
        if ruta.relative_to(APP) != PERMISO
        and (hallados := _usa_el_cliente(ruta.read_text(encoding="utf-8")))
    }
    assert culpables == {}


def test_el_punto_de_paso_si_lo_usa() -> None:
    assert _usa_el_cliente((APP / PERMISO).read_text(encoding="utf-8"))


def test_el_control_positivo_acusa_un_import() -> None:
    fuente = "from app.clients.ppi import ClientePpi\n"
    assert "app.clients.ppi" in _usa_el_cliente(fuente)
