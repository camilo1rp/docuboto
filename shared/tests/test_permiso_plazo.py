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
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]


def test_la_migracion_no_reparte_el_permiso():
    sql = (
        _RAIZ / "services/auth-service/migrations/tenant/021_permiso_plazo.sql"
    ).read_text()
    assert "PERM_DUE_DATE" in sql
    assert "auth_group_permissions" not in sql


def test_el_usuario_de_consulta_no_lo_recibe():
    arbol = ast.parse((_RAIZ / "scripts/seed_demo.py").read_text())
    for nodo in arbol.body:
        nombre = getattr(nodo.targets[0], "id", "") if isinstance(nodo, ast.Assign) else ""
        if nombre == "PERMISOS_CONSULTA":
            nombres = ast.literal_eval(nodo.value)
            assert "PERM_DUE_DATE" not in nombres
            return
    raise AssertionError("PERMISOS_CONSULTA no está en el seed")
