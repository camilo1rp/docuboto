# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de resources y prompts MCP (E18, ADR-019)."""

import pytest

from app import mcp_resources


@pytest.mark.asyncio
async def test_list_resources_y_templates() -> None:
    res = await mcp_resources._list_resources()
    assert any(str(r.uri) == "orfeo://normativa" for r in res)
    tpls = await mcp_resources._list_resource_templates()
    uris = {t.uriTemplate for t in tpls}
    assert "orfeo://radicado/{id}" in uris
    assert "orfeo://expediente/{id}/indice" in uris


@pytest.mark.asyncio
async def test_read_resource_normativa() -> None:
    text = await mcp_resources._read_resource("orfeo://normativa")
    assert "Ley 594/2000" in text and "FUID" in text


@pytest.mark.asyncio
async def test_read_resource_radicado_dispatches(monkeypatch) -> None:
    """Leer orfeo://radicado/{id} consulta el gateway vía la tool del catálogo."""
    captured = {}

    async def fake_call(name, args, client):
        captured["name"] = name
        captured["args"] = args
        return '{"status":200,"data":{"tracking_number":"2024-DEMO-E-000001"}}'

    monkeypatch.setattr(mcp_resources, "call", fake_call)
    rid = "11111111-1111-1111-1111-111111111111"
    text = await mcp_resources._read_resource(f"orfeo://radicado/{rid}")
    assert captured["name"] == "consultar_radicado"
    assert captured["args"] == {"document_id": rid}
    assert "2024-DEMO-E-000001" in text


@pytest.mark.asyncio
async def test_read_resource_indice(monkeypatch) -> None:
    seen = {}

    async def fake_call(name, args, client):
        seen.update(name=name, args=args)
        return "{}"

    monkeypatch.setattr(mcp_resources, "call", fake_call)
    eid = "22222222-2222-2222-2222-222222222222"
    await mcp_resources._read_resource(f"orfeo://expediente/{eid}/indice")
    assert seen["name"] == "indice_expediente" and seen["args"] == {"expediente_id": eid}


@pytest.mark.asyncio
async def test_read_resource_unknown() -> None:
    with pytest.raises(ValueError):
        await mcp_resources._read_resource("orfeo://desconocido/x")


@pytest.mark.asyncio
async def test_list_prompts_y_get_prompt() -> None:
    prompts = await mcp_resources._list_prompts()
    names = {p.name for p in prompts}
    assert {"radicar_pqrsd", "resumen_expediente", "buscar_antecedentes"} <= names

    result = await mcp_resources._get_prompt("resumen_expediente", {"expediente_id": "EXP-1"})
    assert result.messages and result.messages[0].role == "user"
    assert "EXP-1" in result.messages[0].content.text
    assert "orfeo://expediente/EXP-1/indice" in result.messages[0].content.text


@pytest.mark.asyncio
async def test_get_prompt_unknown() -> None:
    with pytest.raises(ValueError):
        await mcp_resources._get_prompt("inexistente", {})
