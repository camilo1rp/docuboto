# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Una variable de entorno retirada que sigue puesta engaña al operador.

`TSA_ESSCERTID_OBLIGATORIO` (booleano) fue sustituida por la fecha de corte
`TSA_ESSCERTID_CORTE`. Quien la heredó cree estar rechazando los sellos sin
ESSCertID; el servicio ya no la lee. Debe decirlo al arrancar.
"""

from __future__ import annotations

from signature.core.config import variables_retiradas_presentes


def test_detecta_la_variable_retirada_y_nombra_a_la_sustituta() -> None:
    assert variables_retiradas_presentes({"TSA_ESSCERTID_OBLIGATORIO": "true"}) == [
        ("TSA_ESSCERTID_OBLIGATORIO", "TSA_ESSCERTID_CORTE")
    ]


def test_sin_la_variable_no_hay_nada_que_avisar() -> None:
    assert variables_retiradas_presentes({"TSA_ESSCERTID_CORTE": "2026-10-04T00:00:00Z"}) == []


