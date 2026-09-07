# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La tarjeta avisa ANTES, no después.

El preparador ya calculaba si el acto es irreversible y qué frase se va a pedir,
y su comentario dice para qué: «para que la tarjeta lo AVISE antes, no después».
El esquema de respuesta descartaba ambos campos en silencio, así que la persona
descubría que el acto no se deshace al ver la frase que tenía que teclear.
"""

from app.schemas.assistant import Propuesta


def _propuesta(**kw):
    base = {
        "id": "p-1",
        "tool": "anular_radicado",
        "operacion": "Anular el radicado 2026-DEMO-E-000123",
        "campos": [],
        "expira_en": 300,
        "token": "t",
    }
    base.update(kw)
    return base


def test_el_aviso_de_irreversible_llega_a_la_tarjeta():
    salida = Propuesta(**_propuesta(irreversible=True, frase_requerida="CONFIRMO"))

    assert salida.irreversible is True
    assert salida.frase_requerida == "CONFIRMO"


def test_un_acto_reversible_no_pide_frase():
    salida = Propuesta(**_propuesta())

    assert salida.irreversible is False
    assert salida.frase_requerida is None


def test_los_campos_sobreviven_al_serializar():
    """El esquema descartaba las claves que no declaraba: se comprueba el
    resultado SERIALIZADO, que es lo que ve la pantalla."""
    d = Propuesta(**_propuesta(irreversible=True, frase_requerida="CONFIRMO")).model_dump()

    assert d["irreversible"] is True
    assert d["frase_requerida"] == "CONFIRMO"
