# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación de argumentos contra el catálogo (`coerce_args`).

Es la barrera real: `additionalProperties: False` del JSON Schema es advisory
para el modelo y no lo aplica nadie del lado del servidor.

Nota de alcance: en este bloque `coerce_args` todavía no está en el camino de
despacho —el asistente conserva su whitelist propia hasta que el dispatcher la
consuma—, así que se prueba directamente. Sin estos tests, la función estaría
escrita y sin verificar, que es la peor de las dos situaciones.
"""

from __future__ import annotations

import pytest

from app.tools.catalog import ArgError, Param, Tool, card_fields, coerce_args, json_schema

_TOOL = Tool(
    name="t",
    description="",
    method="POST",
    path="/api/v1/x/{id}",
    path_params=("id",),
    params=(
        Param(name="id", required=True, location="path", format="uuid", label="Objeto"),
        Param(name="texto", min_length=2, max_length=5, label="Texto"),
        Param(name="n", type="integer", minimum=1, maximum=10, label="Número"),
        Param(name="tipo", enum=("E", "S"), label="Tipo"),
        Param(name="flag", type="boolean", label="Bandera"),
        Param(name="pag", type="integer", show_in_card=False),
    ),
)

_UUID = "11111111-1111-1111-1111-111111111111"


def test_una_clave_no_declarada_se_rechaza_no_se_descarta() -> None:
    """El silencio es el problema: una inyección debe dejar rastro.

    La versión anterior filtraba las claves desconocidas sin decir nada. Es
    cómodo hasta el día en que quien cuela una clave es el contenido de un
    radicado de Entrada, que lo redacta un tercero externo.
    """
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"id": _UUID, "acl": {"max": 3}})
    assert exc.value.code == "parametro_no_declarado"
    assert "acl" in exc.value.detail


def test_falta_un_obligatorio() -> None:
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"texto": "hola"})
    assert exc.value.code == "falta_parametro"


def test_un_uuid_mal_formado_no_llega_al_backend() -> None:
    """Vale por el mensaje, y además es la primera capa contra el traversal."""
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"id": "../../auth/me"})
    assert exc.value.code == "valor_invalido"


def test_un_booleano_no_cuela_como_entero() -> None:
    """`bool` es subclase de `int` en Python: sin la guarda, `True` pasaría."""
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"id": _UUID, "n": True})
    assert exc.value.code == "tipo_invalido"


def test_limites_y_enumeraciones() -> None:
    for malo, code in (
        ({"texto": "a"}, "valor_invalido"),  # por debajo del mínimo
        ({"texto": "abcdef"}, "valor_invalido"),  # por encima del máximo
        ({"n": 0}, "valor_invalido"),
        ({"n": 11}, "valor_invalido"),
        ({"tipo": "X"}, "valor_invalido"),
    ):
        with pytest.raises(ArgError) as exc:
            coerce_args(_TOOL, {"id": _UUID, **malo})
        assert exc.value.code == code, malo


def test_los_opcionales_ausentes_no_viajan() -> None:
    """Un `None` explícito tampoco: enviar `null` a un campo opcional no es
    lo mismo que omitirlo, y algunos backends lo tratan como borrado."""
    salida = coerce_args(_TOOL, {"id": _UUID, "texto": None})
    assert salida == {"id": _UUID}


def test_el_camino_valido_pasa_entero() -> None:
    salida = coerce_args(_TOOL, {"id": _UUID, "texto": "hola", "n": 5, "tipo": "E", "flag": True})
    assert salida == {"id": _UUID, "texto": "hola", "n": 5, "tipo": "E", "flag": True}


def test_la_tarjeta_omite_el_ruido_y_usa_etiquetas() -> None:
    campos = card_fields(_TOOL, {"id": _UUID, "texto": "hola", "pag": 2})
    etiquetas = [c["etiqueta"] for c in campos]
    assert etiquetas == ["Objeto", "Texto"]  # `pag` no se pinta
    assert campos[0]["valor"] == _UUID


def test_el_schema_declara_obligatorios_y_cierra_la_puerta() -> None:
    esquema = json_schema(_TOOL)
    assert esquema["required"] == ["id"]
    assert esquema["additionalProperties"] is False
    assert esquema["properties"]["tipo"]["enum"] == ["E", "S"]
    assert esquema["properties"]["texto"]["minLength"] == 2
