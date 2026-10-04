# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El 422 de metadatos dice QUÉ propiedad falla (AREA-12-05).

`MetadataValidationError` solo llevaba el mensaje en prosa de jsonschema; la
pantalla tenía que adivinar el campo del texto (`'x' is a required property`),
y con un objeto o una lista anidados no había texto que adivinar. Ahora lleva
`field`, la ruta con puntos de la propiedad (`a.b`, `lista.1`).
"""

import pytest

from app.services.metadata_service import MetadataValidationError, validate_metadata

SCHEMA = {
    "type": "object",
    "required": ["valor", "ubicacion"],
    "properties": {
        "valor": {"type": "number"},
        "ubicacion": {
            "type": "object",
            "required": ["ciudad"],
            "properties": {"ciudad": {"type": "string"}, "piso": {"type": "integer"}},
        },
        "etiquetas": {"type": "array", "items": {"type": "string"}},
    },
}

OK = {"valor": 1, "ubicacion": {"ciudad": "Bogota"}}


def _campo(metadata: dict) -> str | None:
    with pytest.raises(MetadataValidationError) as info:
        validate_metadata(metadata, SCHEMA)
    return info.value.field


def test_una_propiedad_requerida_ausente_se_nombra() -> None:
    assert _campo({"ubicacion": {"ciudad": "x"}}) == "valor"


def test_una_propiedad_requerida_ausente_dentro_de_un_objeto_lleva_su_ruta() -> None:
    assert _campo({"valor": 1, "ubicacion": {}}) == "ubicacion.ciudad"


def test_un_tipo_equivocado_dentro_de_un_objeto_lleva_su_ruta() -> None:
    assert _campo({**OK, "ubicacion": {"ciudad": 5}}) == "ubicacion.ciudad"


def test_un_elemento_de_lista_inválido_lleva_su_índice() -> None:
    assert _campo({**OK, "etiquetas": ["a", 7]}) == "etiquetas.1"


def test_metadata_válida_no_lanza() -> None:
    validate_metadata({**OK, "etiquetas": ["a"]}, SCHEMA)


def test_un_fallo_de_la_raíz_no_inventa_campo() -> None:
    with pytest.raises(MetadataValidationError) as info:
        validate_metadata([], SCHEMA)  # type: ignore[arg-type]
    assert info.value.field is None
