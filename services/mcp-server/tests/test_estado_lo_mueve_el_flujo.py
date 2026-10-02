# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-028: el flujo mueve el estado del radicado — lo que cambia en el catálogo.

`actualizar_radicado` deja de ofrecer `status` (el PATCH lo rechaza con 422) y
aparece `finalizar_tramite`, el único acto que cierra un radicado.
"""

from __future__ import annotations

import pytest

from app.tools.catalog import CATALOG, ArgError, coerce_args, get_tool, json_schema

_UUID = "11111111-1111-1111-1111-111111111111"


def test_actualizar_radicado_ya_no_ofrece_estado() -> None:
    tool = get_tool("actualizar_radicado")
    assert tool is not None
    assert "status" not in {p.name for p in tool.params}
    assert "status" not in json_schema(tool)["properties"]
    # Y tampoco lo promete en la descripción como algo editable.
    assert "estado y número de folios" not in tool.description


def test_pasar_status_a_actualizar_radicado_se_rechaza_antes_de_salir() -> None:
    tool = get_tool("actualizar_radicado")
    assert tool is not None
    with pytest.raises(ArgError) as exc:
        coerce_args(tool, {"document_id": _UUID, "status": "closed"})
    assert exc.value.code == "parametro_no_declarado"


def test_finalizar_tramite_esta_declarada_como_irreversible() -> None:
    tool = get_tool("finalizar_tramite")
    assert tool is not None
    assert (tool.method, tool.path) == ("POST", "/api/v1/workflows/{radicado_id}/finalizar")
    assert tool.write is True and tool.destructive is True
    # Nivel 3: espeja `require_permission("PERM_TRAMITAR", min_crud=3)` del backend
    # (el cierre es irreversible; un nivel de lectura no basta).
    assert (tool.permission, tool.min_crud) == ("PERM_TRAMITAR", 3)
    assert tool.epic == "E05"
    assert tool.fallback


def test_finalizar_tramite_exige_comentario_con_el_rango_del_backend() -> None:
    tool = get_tool("finalizar_tramite")
    assert tool is not None
    comentario = next(p for p in tool.params if p.name == "comentario")
    # `FinalizarRequest.comentario`: obligatorio, 5..500.
    assert (comentario.required, comentario.min_length, comentario.max_length) == (True, 5, 500)
    with pytest.raises(ArgError):
        coerce_args(tool, {"radicado_id": _UUID})
    with pytest.raises(ArgError):
        coerce_args(tool, {"radicado_id": _UUID, "comentario": "abc"})
    ok = coerce_args(
        tool, {"radicado_id": _UUID, "comentario": "Trámite concluido", "motivo_cierre": "respondido"}
    )
    assert ok["comentario"] == "Trámite concluido"


def test_finalizar_declara_motivo_cierre_obligatorio_con_el_enum_del_backend() -> None:
    """Sin este parámetro el esquema (`additionalProperties: false`) hacía imposible
    enviarlo y el backend (`FinalizarRequest.motivo_cierre`, obligatorio) respondía
    422 a TODA llamada."""
    tool = get_tool("finalizar_tramite")
    assert tool is not None
    motivo = next(p for p in tool.params if p.name == "motivo_cierre")
    assert motivo.required is True
    assert motivo.enum == ("respondido", "nrr", "traslado_competencia", "otro")
    base = {"radicado_id": _UUID, "comentario": "Trámite concluido"}
    with pytest.raises(ArgError):
        coerce_args(tool, base)  # sin motivo
    with pytest.raises(ArgError):
        coerce_args(tool, {**base, "motivo_cierre": "porque_si"})  # fuera del enum
    for valor in ("respondido", "nrr", "traslado_competencia", "otro"):
        assert coerce_args(tool, {**base, "motivo_cierre": valor})["motivo_cierre"] == valor


def test_el_esquema_json_de_finalizar_publica_el_motivo_como_obligatorio() -> None:
    tool = get_tool("finalizar_tramite")
    assert tool is not None
    esquema = json_schema(tool)
    assert esquema["properties"]["motivo_cierre"]["enum"] == [
        "respondido",
        "nrr",
        "traslado_competencia",
        "otro",
    ]
    assert "motivo_cierre" in esquema["required"]
    assert esquema.get("additionalProperties") is False


def test_es_la_unica_tool_del_catalogo_que_cierra_un_radicado() -> None:
    """Nadie más cierra: ninguna otra tool de escritura sobre el radicado ofrece
    un `status` de cierre. Si alguna lo volviera a declarar, el backend la
    rechazaría con 422 — se detecta aquí, no en producción."""
    for t in CATALOG:
        if t.path.startswith("/api/v1/documents/") and t.method == "PATCH":
            assert "status" not in {p.name for p in t.params}, t.name


def test_finalizar_se_confirma_con_el_radicado_concreto_no_con_la_frase_fija() -> None:
    """Escalera de confirmación: una frase fija se teclea de memoria antes de leer y
    confirma «un cierre», no «ESTE cierre». Hay que reproducir el radicado."""
    from app.services.write_gate import FRASE_IRREVERSIBLE, frase_requerida, frase_valida

    tool = get_tool("finalizar_tramite")
    assert tool is not None and tool.confirmar_con == "radicado_id"
    args = {"radicado_id": _UUID, "comentario": "Trámite concluido", "motivo_cierre": "respondido"}
    exigida = frase_requerida(tool, args)
    assert exigida == _UUID
    assert not frase_valida(FRASE_IRREVERSIBLE, exigida)  # `CONFIRMO` ya no basta
    assert not frase_valida("22222222-2222-2222-2222-222222222222", exigida)  # otro radicado
    assert frase_valida(_UUID.upper(), exigida)  # sin distinguir mayúsculas (como el resto)


def test_la_tarjeta_de_finalizar_avisa_de_la_frase_antes_de_confirmar() -> None:
    from app.services.write_gate import preparar

    tarjeta = preparar(
        get_tool("finalizar_tramite"),  # type: ignore[arg-type]
        {"radicado_id": _UUID, "comentario": "Trámite concluido", "motivo_cierre": "respondido"},
        sub="u1",
        ten="demo",
    )
    assert tarjeta["proposal"]["irreversible"] is True
    assert tarjeta["proposal"]["frase_requerida"] == _UUID
