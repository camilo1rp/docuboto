# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-4 (ADR-040): el catálogo MCP habla el contrato nuevo de la TRD, la TVD y los tipos.

archive-service rechaza ahora lo desconocido (`extra="forbid"` → 422) y exige las dos fases de
retención: una tool que siguiera anunciando `retention_years`/`total_retention` sería una tool rota
(el modelo la llenaría y el backend la devolvería con 422), y una TVD sin `archivo_central_years`
nacería con calendario 0."""

from __future__ import annotations

import pytest

from app.tools.catalog import ArgError, coerce_args, get_tool

_LEGADOS = {"retention_years", "total_retention"}
_TRD_ESCRITURA = ("crear_serie_trd", "nueva_version_serie_trd", "crear_agrupacion_tvd", "nueva_version_agrupacion_tvd")


def _nombres(tool_name: str) -> dict[str, object]:
    return {p.name: p for p in get_tool(tool_name).params}


@pytest.mark.parametrize("nombre", _TRD_ESCRITURA)
def test_ninguna_tool_de_instrumentos_anuncia_el_juego_legado(nombre: str) -> None:
    assert not _LEGADOS & set(_nombres(nombre)), nombre


def test_crear_serie_exige_las_dos_fases_sin_valor_por_omision() -> None:
    p = _nombres("crear_serie_trd")
    for fase in ("archivo_gestion_years", "archivo_central_years"):
        assert p[fase].required and p[fase].minimum == 0, fase
    with pytest.raises(ArgError) as exc:
        coerce_args(get_tool("crear_serie_trd"), {"code": "X", "name": "n", "archivo_gestion_years": 2})
    assert exc.value.code == "falta_parametro"
    assert "archivo_central_years" in exc.value.detail


def test_crear_serie_acepta_la_productora_como_lista_de_enteros() -> None:
    args = coerce_args(
        get_tool("crear_serie_trd"),
        {
            "code": "X",
            "name": "n",
            "archivo_gestion_years": 2,
            "archivo_central_years": 8,
            "dependencias_productoras": [110, 120],
        },
    )
    assert args["dependencias_productoras"] == [110, 120]


def test_la_version_nueva_hereda_las_fases_y_la_productora_si_se_omiten() -> None:
    p = _nombres("nueva_version_serie_trd")
    for campo in ("archivo_gestion_years", "archivo_central_years", "dependencias_productoras"):
        assert campo in p and not p[campo].required, campo


def test_crear_agrupacion_tvd_exige_la_fase_central_y_el_contenido_valorado() -> None:
    """La TVD no tiene gestión: su total es su fase central. Y el backend ya exigía el resto del
    contenido valorado; la tool no lo declaraba y por tanto nunca podía crear una."""
    p = _nombres("crear_agrupacion_tvd")
    for campo in (
        "archivo_central_years",
        "fondo_nombre",
        "fecha_extrema_inicial",
        "fecha_extrema_final",
        "justificacion_valoracion",
    ):
        assert p[campo].required, campo
    assert "archivo_gestion_years" not in p


def test_crear_tipo_documental_distingue_publicable_de_la_ventanilla() -> None:
    p = _nombres("crear_tipo_documental")
    assert p["publicable"].type == "boolean" and not p["publicable"].required
    assert p["admite_ventanilla_publica"].type == "boolean"
    # el modelo debe poder leer la diferencia en la descripción, no solo en el nombre
    assert "difusión" in p["publicable"].description and "nivel 3" in p["publicable"].description
    assert "NO implica que se publique" in p["admite_ventanilla_publica"].description
    # la tool se anuncia con nivel 1, pero `publicable` pide 3: el modelo debe saberlo antes de fallar
    assert "nivel 3" in get_tool("crear_tipo_documental").description


def test_cuadro_clasificacion_es_una_lectura_del_endpoint_nuevo() -> None:
    t = get_tool("cuadro_clasificacion")
    assert (t.method, t.path) == ("GET", "/api/v1/trd/cuadro")
    assert not t.write and t.epic == "E04"
    assert {p.name for p in t.params} == {"incluir_borradores", "incluir_vacias", "segun"}
    assert all(not p.required for p in t.params)
    assert dict((p.name, p.enum) for p in t.params)["segun"] == ("convalidado", "vivo")
