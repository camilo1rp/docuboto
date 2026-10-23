# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""ADR-037: las tres tools de conocimiento (E21) dependen de la bandera de la entidad y
de `PERM_CONOCIMIENTO`; el resto del catálogo no cambia con la bandera.
"""

from __future__ import annotations

import pytest

from app.services import dispatcher
from app.tools.catalog import CATALOG, get_tool, puede_usar, visible_tools
from app.tools.procedures import procedimientos_visibles

CONOCIMIENTO = {"buscar_conocimiento", "buscar_antecedentes", "redactar_con_antecedentes"}


def _nombres(**kw: object) -> set[str]:
    return {t.name for t in visible_tools(**kw)}  # type: ignore[arg-type]


def test_las_tres_tools_existen_y_declaran_el_permiso() -> None:
    assert {t.name for t in CATALOG if t.epic == "E21"} == CONOCIMIENTO
    for nombre in CONOCIMIENTO:
        tool = get_tool(nombre)
        assert tool is not None and tool.permission == "PERM_CONOCIMIENTO" and tool.min_crud == 1


def test_con_permiso_y_bandera_se_muestran() -> None:
    """Control positivo."""
    visibles = _nombres(
        permissions={"PERM_CONOCIMIENTO"},
        levels={"PERM_CONOCIMIENTO": 1},
        asistente_habilitado=True,
    )
    assert CONOCIMIENTO <= visibles


def test_con_permiso_y_bandera_apagada_se_ocultan() -> None:
    visibles = _nombres(
        permissions={"PERM_CONOCIMIENTO"},
        levels={"PERM_CONOCIMIENTO": 1},
        asistente_habilitado=False,
    )
    assert not (CONOCIMIENTO & visibles)


def test_con_bandera_y_sin_permiso_se_ocultan() -> None:
    visibles = _nombres(permissions=set(), levels={}, asistente_habilitado=True)
    assert not (CONOCIMIENTO & visibles)


def test_root_con_bandera_apagada_tampoco_las_ve_pero_con_ella_si() -> None:
    assert not (CONOCIMIENTO & _nombres(permissions=set(), is_root=True, asistente_habilitado=False))
    assert CONOCIMIENTO <= _nombres(permissions=set(), is_root=True, asistente_habilitado=True)


def test_olvidar_el_parametro_falla_cerrado() -> None:
    """El default es `False`: quien no lo pase esconde las tools, no las ofrece."""
    assert not (CONOCIMIENTO & _nombres(permissions={"PERM_CONOCIMIENTO"}, is_root=True))


def test_el_resto_del_catalogo_no_cambia_con_la_bandera() -> None:
    kw = dict(permissions={"PERM_RADI", "USUA_PERM_EXPEDIENTE"}, is_root=False)
    con = _nombres(**kw, asistente_habilitado=True) - CONOCIMIENTO
    sin = _nombres(**kw, asistente_habilitado=False) - CONOCIMIENTO
    assert con == sin and con, "la bandera movió tools que no son de conocimiento"
    # Y root: todo lo que no es de conocimiento sigue igual.
    assert _nombres(permissions=set(), is_root=True, asistente_habilitado=True) - CONOCIMIENTO == (
        _nombres(permissions=set(), is_root=True, asistente_habilitado=False)
    )


@pytest.mark.parametrize("nombre", sorted(CONOCIMIENTO))
def test_el_dispatcher_las_niega_con_la_bandera_apagada(nombre: str) -> None:
    tool = get_tool(nombre)
    assert tool is not None
    for root in (False, True):
        with pytest.raises(dispatcher.DispatchError) as exc:
            dispatcher.authorize(
                tool,
                permissions={"PERM_CONOCIMIENTO"},
                is_root=root,
                levels={"PERM_CONOCIMIENTO": 3},
                asistente_habilitado=False,
            )
        assert (exc.value.code, exc.value.status) == ("asistente_deshabilitado", 403)
    # Control positivo: con la bandera, el mismo llamante pasa.
    dispatcher.authorize(
        tool,
        permissions={"PERM_CONOCIMIENTO"},
        is_root=False,
        levels={"PERM_CONOCIMIENTO": 1},
        asistente_habilitado=True,
    )


def test_el_dispatcher_exige_el_permiso_aun_con_la_bandera() -> None:
    tool = get_tool("buscar_conocimiento")
    assert tool is not None
    with pytest.raises(dispatcher.DispatchError) as exc:
        dispatcher.authorize(
            tool, permissions=set(), is_root=False, levels={}, asistente_habilitado=True
        )
    assert exc.value.code == "permiso_insuficiente"


def test_los_procedimientos_no_dependen_de_la_bandera_si_no_usan_conocimiento() -> None:
    kw = dict(permissions={"PERM_RADI", "USUA_PERM_EXPEDIENTE", "PERM_TRAMITAR"}, is_root=True)
    assert [p.name for p in procedimientos_visibles(**kw, asistente_habilitado=True)] == [
        p.name for p in procedimientos_visibles(**kw, asistente_habilitado=False)
    ]


def test_puede_usar_aplica_la_bandera() -> None:
    tool = get_tool("buscar_conocimiento")
    assert tool is not None
    assert puede_usar(tool, {"PERM_CONOCIMIENTO"}, levels={"PERM_CONOCIMIENTO": 1}, asistente_habilitado=True)
    assert not puede_usar(tool, {"PERM_CONOCIMIENTO"}, levels={"PERM_CONOCIMIENTO": 1})
