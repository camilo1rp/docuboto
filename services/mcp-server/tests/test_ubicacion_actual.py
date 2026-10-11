# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-029: dónde está el radicado AHORA — lo que cambia en el catálogo.

Listar y buscar ganan el filtro `ubicacion_dept_code`; y `dest_dept[_code]` se
describe como la dependencia DE RADICACIÓN (a quién iba dirigido), que repartir o
devolver no cambia. Sin esto el modelo filtraría «lo que tiene Control Interno»
por `dest_dept_code` y obtendría lo que se le DIRIGIÓ, que es otra pregunta.
"""

from __future__ import annotations

import pytest

from app.tools.catalog import ArgError, coerce_args, get_tool, json_schema


@pytest.mark.parametrize("nombre", ["listar_radicados", "buscar_radicados"])
def test_listar_y_buscar_filtran_por_ubicacion_actual(nombre: str) -> None:
    tool = get_tool(nombre)
    assert tool is not None
    param = next(p for p in tool.params if p.name == "ubicacion_dept_code")
    # Mismo rango que `Query(ge=1, le=999999)` del backend.
    assert (param.type, param.minimum, param.maximum) == ("integer", 1, 999999)
    assert "dest_dept_code" in param.description  # distingue las dos preguntas
    assert json_schema(tool)["properties"]["ubicacion_dept_code"]["type"] == "integer"
    assert coerce_args(tool, {"ubicacion_dept_code": 500})["ubicacion_dept_code"] == 500


@pytest.mark.parametrize("valor", [0, 1_000_000])
def test_la_ubicacion_fuera_de_rango_se_rechaza_antes_de_salir(valor: int) -> None:
    tool = get_tool("listar_radicados")
    assert tool is not None
    with pytest.raises(ArgError):
        coerce_args(tool, {"ubicacion_dept_code": valor})


@pytest.mark.parametrize(
    ("herramienta", "param"),
    [("buscar_radicados", "dest_dept_code"), ("actualizar_radicado", "dest_dept_code"),
     ("radicar_documento", "dest_dept_code")],
)
def test_el_destino_se_describe_como_de_radicacion(herramienta: str, param: str) -> None:
    tool = get_tool(herramienta)
    assert tool is not None
    p = next(p for p in tool.params if p.name == param)
    assert "RADICACI" in p.description.upper()
