# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""PERM_DUE_DATE existe y no se concede solo al migrar."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests._chain import chain_text, seed_rows

_RAIZ = Path(__file__).resolve().parents[2]


def test_la_migracion_no_reparte_el_permiso():
    assert "('PERM_DUE_DATE'," in seed_rows("auth_permissions")
    # La cadena crea el permiso y no siembra ninguna concesión.
    assert re.findall(r"INSERT INTO auth_group_permissions\b", chain_text("tenant")) == []


def test_el_usuario_de_consulta_no_lo_recibe():
    arbol = ast.parse((_RAIZ / "scripts/seed_demo.py").read_text())
    for nodo in arbol.body:
        nombre = getattr(nodo.targets[0], "id", "") if isinstance(nodo, ast.Assign) else ""
        if nombre == "PERMISOS_CONSULTA":
            nombres = ast.literal_eval(nodo.value)
            assert "PERM_DUE_DATE" not in nombres
            return
    raise AssertionError("PERMISOS_CONSULTA no está en el seed")
