# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""PPI 1.4.0 is frozen in its own directory and carries only what was built (ADR-051 §3).

A choice field without options is still not a valid extract request.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tests._compose import RAIZ, cargar

VERSION = "1.4.0"
PPI = RAIZ / "contracts" / "ppi" / f"v{VERSION}"
OPENAPI = PPI / "ppi-v1.openapi.yaml"
MANIFEST = PPI / "examples" / "manifest.json"
SUMS = PPI / "SHA256SUMS"


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


def test_el_openapi_de_ppi_carga_en_1_4_0() -> None:
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


def test_el_bucle_de_recomendaciones_no_se_publica() -> None:
    """Nadie construyó runs, decisions ni recommend: el contrato no los declara."""
    spec = _spec()
    assert set(spec["paths"]) == {
        "/v1/capabilities",
        "/v1/connection",
        "/v1/ingest",
        "/v1/ingest/purge",
        "/v1/ingest/inventory",
        "/v1/ingest/{external_ref}/status",
        "/v1/extract",
        "/v1/query",
    }
    assert set(spec["webhooks"]) == {"content"}
    callbacks = spec["components"]["schemas"]["Connection"]["properties"]["callbacks"]
    assert "recommend_url" not in callbacks["properties"]
    assert "recommend_url" not in callbacks["required"]
    assert callbacks["additionalProperties"] is False
    for nombre in ("RunRequest", "RunStatus", "RecommendationBatch", "DecisionBatch"):
        assert nombre not in spec["components"]["schemas"]
    assert not (PPI / "schemas" / "recommendation.schema.json").exists()
    assert not (PPI / "schemas" / "decision.schema.json").exists()


def test_la_cabecera_acepta_cualquier_1_x() -> None:
    spec = _spec()
    patron = spec["components"]["parameters"]["PpiVersion"]["schema"]["pattern"]
    assert patron == r"^1\.\d+\.\d+$"


def test_el_manifiesto_lista_cada_fixture() -> None:
    listados = {item["file"] for item in _manifest()["fixtures"]}
    en_disco = {
        str(p.relative_to(PPI))
        for p in (PPI / "examples").rglob("*.json")
        if p.name != "manifest.json"
    }
    assert listados == en_disco
    assert all(not item["schema"].startswith(("schemas/recommendation", "schemas/decision"))
               for item in _manifest()["fixtures"])


def test_el_directorio_publicado_no_cambia() -> None:
    """Un directorio publicado es inmutable: una errata es una versión nueva."""
    esperadas = {}
    for linea in SUMS.read_text(encoding="utf-8").splitlines():
        suma, _, ruta = linea.partition("  ")
        esperadas[ruta.removeprefix("./")] = suma
    reales = {
        str(p.relative_to(PPI)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in PPI.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS"
    }
    assert reales == esperadas, (
        f"contracts/ppi/v{VERSION}/ cambió. Publica una versión nueva en su propio "
        "directorio en vez de editar esta."
    )


def test_no_queda_la_copia_sin_version() -> None:
    assert not (RAIZ / "docs" / "contratos" / "ppi").exists()
