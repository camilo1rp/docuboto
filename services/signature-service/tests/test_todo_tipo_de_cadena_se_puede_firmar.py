# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Un tipo de cadena que se puede abrir tiene que poderse firmar.

`documento` vivía en el allow-set de `objeto_tipo` **sin productor**: ningún
servicio crea, guarda ni resuelve un objeto de ese tipo. Exigía huella de
contenido (`CADENA_PAYLOAD_OBLIGATORIO`) y no había nadie que pudiera releerla,
así que el gate de vigencia lo buscaba como si fuera un borrador, no lo
encontraba y devolvía `409 documento_desaparecido` — diciendo además que era «un
borrador que ya no existe» sobre algo que jamás lo fue.

Medido contra el sistema levantado antes de retirarlo: crear devolvía **201** y
firmar **409**, sin salida. Un camino declarado que no puede completarse.

Nada ataba las dos mitades del trato —«exijo huella» y «sé resolverla»—, así que
la contradicción no la delataba ningún test. Estos guardianes las cruzan por
ADMISIÓN: añadir un tipo obliga a decir de qué lado está, o esto se cae.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.constants import (
    CADENA_OBJETO_TIPOS,
    CADENA_PAYLOAD_AUTORITATIVO,
    CADENA_PAYLOAD_OBLIGATORIO,
    CADENA_SIN_HUELLA_DECLARADA,
)
from app.schemas.cadena import SolicitudCreate
from app.services.cadena_service import RESOLUTOR_DE_HUELLA

FIRMANTE = "11111111-1111-1111-1111-111111111111"


def test_quien_exige_huella_tiene_quien_se_la_resuelva() -> None:
    """Las dos mitades del trato, atadas.

    Exigir huella de contenido sin resolutor produce una cadena que se crea y
    no se firma jamás. Es una igualdad y no una inclusión: un resolutor para un
    tipo que no exige huella tampoco tiene sentido —nadie lo llamaría—.
    """
    assert set(RESOLUTOR_DE_HUELLA) == set(CADENA_PAYLOAD_OBLIGATORIO)


def test_cada_tipo_admitido_declara_de_que_lado_esta() -> None:
    """Partición exacta, escrita por admisión.

    Un tipo nuevo que no entre en ninguno de los dos conjuntos es justo el caso
    de `documento`: admitido arriba y sin decidir abajo.
    """
    con_huella = set(CADENA_PAYLOAD_OBLIGATORIO)
    sin_huella = set(CADENA_SIN_HUELLA_DECLARADA)
    assert con_huella & sin_huella == set(), "un tipo no puede estar en los dos lados"
    assert con_huella | sin_huella == set(CADENA_OBJETO_TIPOS)


def test_lo_que_exige_huella_sin_fuente_autoritativa_se_la_pide_al_llamante() -> None:
    """Y hoy ese conjunto está VACÍO, que es el estado bueno.

    Pedirle la huella al llamante es pedirle que se la invente: es el gesto que
    todo este subsistema existe para impedir. Mientras la resta salga vacía,
    ningún tipo puede llegar a pedírsela.
    """
    assert set(CADENA_PAYLOAD_OBLIGATORIO) - set(CADENA_PAYLOAD_AUTORITATIVO) == set()


def test_documento_ya_no_se_puede_abrir() -> None:
    """El tipo fantasma, cerrado en la puerta de entrada.

    No basta con quitarle la pantalla: mientras el schema lo admitiera, el
    cliente del API podía seguir fabricando cadenas muertas.
    """
    with pytest.raises(ValidationError):
        SolicitudCreate(
            objeto_tipo="documento",
            objeto_id="298ddde9-d43f-4aca-b908-cf223ff20398",
            payload_sha256="c" * 64,
            firmantes=[FIRMANTE],
        )


@pytest.mark.parametrize("tipo", sorted(CADENA_OBJETO_TIPOS))
def test_los_tipos_que_quedan_si_se_pueden_abrir(tipo: str) -> None:
    """Control positivo: la puerta no se cerró de más."""
    sol = SolicitudCreate(
        objeto_tipo=tipo,
        objeto_id="298ddde9-d43f-4aca-b908-cf223ff20398",
        firmantes=[FIRMANTE],
    )
    assert sol.objeto_tipo == tipo
