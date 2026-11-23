# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La declaración nombra lo que viene del proveedor, no un nombre del código."""

from tenant.core.declaracion import (
    exigidos_nivel,
    exigidos_proveedor,
    faltan,
    ruta_admitida,
)

VISTA = {
    "provider": {"id": "example-provider", "name": "Example Provider"},
    "subprocessors": [{"id": "model-host-a", "name": "Example Model Host"}],
    "routing": [
        {
            "level": "public",
            "hosting": "hosted",
            "model_aliases": ["triage-small"],
            "subprocessors": ["model-host-a"],
            "prompt_and_output_retention": "none",
            "trains_on_customer_data": False,
        },
        {
            "level": "reserved",
            "hosting": "installation",
            "model_aliases": ["drafting-large"],
            "subprocessors": [],
            "prompt_and_output_retention": "none",
            "trains_on_customer_data": False,
        },
    ],
}


def test_el_proveedor_exige_id_y_nombre_de_cada_subencargado() -> None:
    assert exigidos_proveedor(VISTA) == [
        "Example Provider",
        "example-provider",
        "Example Model Host",
        "model-host-a",
    ]


def test_el_nivel_exige_la_ruta_los_modelos_y_sus_subencargados() -> None:
    assert exigidos_nivel(VISTA, 1) == [
        "hosted",
        "triage-small",
        "model-host-a",
        "Example Model Host",
    ]
    assert exigidos_nivel(VISTA, 2) == ["installation", "drafting-large"]
    assert exigidos_nivel(VISTA, 3) is None


def test_faltan_conserva_el_orden_y_no_repite() -> None:
    assert faltan("Example Provider", exigidos_proveedor(VISTA)) == [
        "example-provider",
        "Example Model Host",
        "model-host-a",
    ]


def test_un_modelo_alojado_que_entrena_no_se_admite() -> None:
    fila = dict(VISTA["routing"][0])
    assert ruta_admitida(fila) is True
    fila["trains_on_customer_data"] = True
    assert ruta_admitida(fila) is False
    fila["trains_on_customer_data"] = False
    fila["prompt_and_output_retention"] = "limited"
    assert ruta_admitida(fila) is False
    assert ruta_admitida(VISTA["routing"][1]) is True
