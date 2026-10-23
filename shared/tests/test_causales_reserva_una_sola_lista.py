# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El catálogo de causales de reserva vive en TRES sitios: que digan lo mismo (C12, ADR-039).

La lista cerrada de la Ley 1712/2014 arts. 18-19 está en `document-service`
(`app/core/causales_reserva.py`, que manda), en el CHECK de su migración 050 y en el `enum`
de la tool MCP `reclasificar_radicado`. Una lista de admisión que crece aparte de quien la
consume acaba cubriendo lo que alguien recordó: aquí se compara, desde fuera de los dos
servicios, que no diverjan. (Los dos primeros ya se comparan dentro de document-service; este
añade el tercero, que solo se ve desde el repositorio.)
"""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_DOC = _RAIZ / "services" / "document-service"
_CATALOGO_MCP = _RAIZ / "services" / "mcp-server" / "app" / "tools" / "catalog.py"
_SQL = _DOC / "migrations" / "tenant" / "050_reserva_catalogo_y_desclasificacion.sql"
_CAUSALES_PY = _DOC / "app" / "core" / "causales_reserva.py"

pytestmark = pytest.mark.skipif(
    not (_SQL.is_file() and _CATALOGO_MCP.is_file() and _CAUSALES_PY.is_file()),
    reason="el árbol de los servicios no está montado",
)


def _constante_python() -> tuple[set[str], int]:
    spec = importlib.util.spec_from_file_location("causales_reserva_doc", _CAUSALES_PY)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = modulo  # los dataclasses con slots lo necesitan
    spec.loader.exec_module(modulo)
    return set(modulo.CAUSALES), modulo.NIVEL_DOBLE_FIRMA


def _enum_mcp() -> set[str]:
    arbol = ast.parse(_CATALOGO_MCP.read_text())
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.AnnAssign) and getattr(nodo.target, "id", "") == "CAUSALES_RESERVA":
            return {e.value for e in nodo.value.elts}  # type: ignore[union-attr]
        if isinstance(nodo, ast.Assign) and any(
            getattr(t, "id", "") == "CAUSALES_RESERVA" for t in nodo.targets
        ):
            return {e.value for e in nodo.value.elts}  # type: ignore[attr-defined]
    raise AssertionError("no se encontró CAUSALES_RESERVA en el catálogo MCP")


def _check_sql() -> set[str]:
    texto = _SQL.read_text()
    i = texto.index("ADD CONSTRAINT radicados_causal_catalogo")
    return set(re.findall(r"'((?:art18|art19)_[a-z_]+)'", texto[i : texto.index("NOT VALID", i)]))


def test_codigo_check_y_enum_mcp_listan_las_mismas_causales() -> None:
    py, _ = _constante_python()
    assert py == _check_sql(), "la constante de Python y el CHECK de la migración 050 divergen"
    assert py == _enum_mcp(), "la constante de Python y el enum de la tool MCP divergen"


def test_la_tool_de_reclasificar_y_la_de_desclasificar_usan_el_mismo_enum() -> None:
    fuente = _CATALOGO_MCP.read_text()
    assert fuente.count("_P_CAUSAL_RESERVA,") >= 2, (
        "reclasificar_radicado y solicitar_desclasificacion deben compartir el mismo Param: "
        "dos enum escritos aparte divergen"
    )


def test_el_umbral_de_doble_firma_del_mcp_no_se_contradice() -> None:
    """La descripción del MCP manda bajar desde Clasificada (3) por la solicitud: si el
    umbral cambiara, la tool tiene que decir otra cosa."""
    _, umbral = _constante_python()
    assert umbral == 3
    assert "solicitar_desclasificacion" in _CATALOGO_MCP.read_text()
