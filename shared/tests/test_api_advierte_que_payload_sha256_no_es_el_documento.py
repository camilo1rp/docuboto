# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
El contrato público de la firma dice qué ES ``payload_sha256`` (AREA-05-07).

``FirmaResponse.payload_sha256`` es el SHA-256 de los BYTES QUE SE FIRMARON, y eso
no es el documento: para ``xades_personal`` son los del envoltorio ``<acto …>``,
para la firma nativa de un borrador es el hash del texto hexadecimal de la
huella, y para radicado/expediente el digest de ``"tipo:id"``. Solo es comparable
entre firmas del mismo ``provider``. Quien lo toma por «el hash de mi PDF» y lo
coteja con el de un fichero en la mano concluye que la firma no corresponde a su
documento, o —peor— que sí.

La advertencia existía en el código y en el changelog, pero no en el contrato que
lee el integrador (``docs/*/api.md``). Este guardián la ata a la línea donde el
endpoint lista el campo: si alguien reescribe esa descripción y se lleva la
frase, el contrato vuelve a callar justo lo que importa.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]

#: (idioma, frase de la advertencia). Se busca en la MISMA línea que el campo
#: listado en la respuesta de ``sign-xml`` (``{firma_id, payload_sha256, …}``).
_ADVERTENCIAS = [
    ("es", "solo es comparable entre firmas del mismo `provider`"),
    ("en", "only comparable between signatures of the same `provider`"),
]
_NO_SIRVE = [
    ("es", "no sirve para cotejar un PDF en la mano"),
    ("en", "cannot be used to check against a PDF in hand"),
]


def _linea_de_sign_xml(idioma: str) -> str:
    texto = (_RAIZ / "docs" / idioma / "api.md").read_text(encoding="utf-8")
    lineas = [
        ln
        for ln in texto.splitlines()
        if ln.startswith("- `POST /api/v1/signature/sign-xml`") and "{firma_id, payload_sha256," in ln
    ]
    assert len(lineas) == 1, (
        f"docs/{idioma}/api.md: no hay exactamente una descripción de sign-xml que liste "
        "`payload_sha256` en la respuesta"
    )
    return lineas[0]


@pytest.mark.parametrize(("idioma", "frase"), _ADVERTENCIAS + _NO_SIRVE)
def test_sign_xml_advierte_que_payload_sha256_no_es_el_documento(idioma: str, frase: str) -> None:
    assert frase in _linea_de_sign_xml(idioma), (
        f"docs/{idioma}/api.md: la descripción de sign-xml lista `payload_sha256` pero ya no "
        f"advierte {frase!r}. Es el SHA-256 de los bytes firmados, no del documento."
    )
