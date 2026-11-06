# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""PPI 1.3.0 loads, and a choice field without options is not a valid extract request."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests._compose import RAIZ, cargar

PPI = RAIZ / "docs" / "contratos" / "ppi"
OPENAPI = PPI / "ppi-v1.openapi.yaml"
MANIFEST = PPI / "examples" / "manifest.json"
VERSION = "1.3.0"


def _spec() -> dict[str, Any]:
    return cargar(OPENAPI)


def _manifest() -> dict[str, Any]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _choice_branch(spec: dict[str, Any]) -> dict[str, Any]:
    field = spec["components"]["schemas"]["ExtractField"]
    for option in field["oneOf"]:
        kind = option.get("properties", {}).get("kind", {})
        if kind.get("const") == "choice":
            assert isinstance(option, dict)
            return option
    raise AssertionError("ExtractField has no choice branch")


def _field_matches(field: dict[str, Any], branch: dict[str, Any]) -> bool:
    required = branch.get("required") or []
    if any(clave not in field for clave in required):
        return False
    properties = branch.get("properties") or {}
    if set(field) - set(properties):
        return False
    kind = field.get("kind")
    esperado = properties.get("kind") or {}
    if "const" in esperado and kind != esperado["const"]:
        return False
    if "enum" in esperado and kind not in esperado["enum"]:
        return False
    choices_schema = properties.get("choices")
    if not isinstance(choices_schema, dict):
        return "choices" not in field
    choices = field.get("choices")
    minimo = int(choices_schema.get("minItems") or 0)
    return isinstance(choices, list) and len(choices) >= minimo


def _extract_request_ok(payload: dict[str, Any], spec: dict[str, Any]) -> bool:
    if not isinstance(payload.get("external_ref"), str) or not payload["external_ref"]:
        return False
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        return False
    branches = spec["components"]["schemas"]["ExtractField"]["oneOf"]
    return all(
        isinstance(field, dict) and any(_field_matches(field, branch) for branch in branches)
        for field in fields
    )


def test_el_openapi_de_ppi_carga_en_1_3_0() -> None:
    spec = _spec()
    assert spec["info"]["version"] == VERSION
    assert spec["openapi"] == "3.1.0"
    rutas = spec["paths"]
    assert "/v1/ingest/{external_ref}/status" in rutas
    assert "/v1/extract" in rutas
    assert _manifest()["protocol_version"] == VERSION


def test_el_campo_choice_exige_al_menos_una_opcion() -> None:
    spec = _spec()
    rama = _choice_branch(spec)
    assert "choices" in rama["required"]
    assert rama["properties"]["choices"]["minItems"] == 1
    payload = json.loads(
        (PPI / "examples" / "invalid" / "extract-choice-without-options.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["fields"][0]["choices"] == []
    assert _extract_request_ok(payload, spec) is False


def test_el_pedido_de_extract_valido_pasa() -> None:
    spec = _spec()
    payload = json.loads(
        (PPI / "examples" / "valid" / "extract-request.json").read_text(encoding="utf-8")
    )
    assert _extract_request_ok(payload, spec) is True


def test_el_manifiesto_declara_el_rechazo_de_choice_sin_opciones() -> None:
    entradas = [
        item
        for item in _manifest()["fixtures"]
        if item["file"] == "examples/invalid/extract-choice-without-options.json"
    ]
    assert len(entradas) == 1
    assert entradas[0]["valid"] is False
    assert entradas[0]["schema"].endswith("ExtractRequest")
