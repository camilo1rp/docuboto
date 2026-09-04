# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Guardián: una clave de auditoría llamada `hash_documento` tiene que llevar la
huella del DOCUMENTO.

El asiento `firma.turno_firmado` publicaba bajo ese nombre el
`payload_sha256` que devuelve el firmante, que es el SHA-256 de los BYTES
FIRMADOS. Para un `borrador` esos bytes son el texto hexadecimal de la huella,
así que el valor era el hash del hash — y el asiento afirmaba «hash del
documento» justo donde el sistema tiene que poder demostrar QUÉ se firmó
(Ley 594/2000, trazabilidad del acto).

No se descubrió leyendo el código sino comparando dos asientos consecutivos en
`audit_log`: `firma.challenge.emitido` decía `49fff365…` y `firma.turno_firmado`
decía `cd51626b…` sobre el mismo acto. Dos valores distintos bajo el mismo
nombre, para un observador que audita.

Este guardián no compara valores en tiempo de ejecución —eso exigiría un firmante
real— sino la FUENTE: prohíbe que la clave `hash_documento` se alimente de un
resultado de firma. Es una comprobación de forma, y a propósito: el defecto es de
NOMBRE, y un nombre solo se puede vigilar en el sitio donde se escribe.
"""

from __future__ import annotations

import re
from pathlib import Path

CADENA = Path(__file__).resolve().parents[1] / "app" / "services" / "cadena_service.py"

# Lo que NUNCA puede alimentar una clave llamada `hash_documento`: cualquier
# cosa que salga de firmar. `sign_result`, `signed`, `personal_signed`...
_RESULTADO_DE_FIRMA = re.compile(
    r'"hash_documento"\s*:\s*[a-z_]*(sign_result|signed|firma_result)[a-z_]*\b'
)

# Fuentes legítimas: la solicitud (huella del documento), el challenge (atado a
# ella al emitirse) o el resolutor compartido.
_FUENTES_LEGITIMAS = (
    "huella_para_desafio(sol)",          # el resolutor compartido
    'challenge["hash_documento"]',       # atado al emitir, ya resuelto
    "hash_documento",                    # la variable local de emitir_desafio
)


def test_hash_documento_nunca_sale_de_un_resultado_de_firma() -> None:
    fuente = CADENA.read_text(encoding="utf-8")
    infracciones = _RESULTADO_DE_FIRMA.findall(fuente)
    assert not infracciones, (
        "una clave de auditoría llamada `hash_documento` se está alimentando del "
        f"resultado de firmar ({infracciones}): ese valor es el SHA-256 de los "
        "BYTES FIRMADOS, que para un borrador es el hash del hash. Publícalo como "
        "`payload_firmado_sha256`, o toma la huella real de la solicitud."
    )


def test_todas_las_claves_hash_documento_vienen_de_una_fuente_conocida() -> None:
    """Anti-vacuidad y anti-deriva a la vez.

    Si mañana alguien alimenta `hash_documento` desde una variable nueva, este
    test cae aunque no case con el patrón del anterior. Un guardián que solo
    prohíbe lo ya visto deja de servir en cuanto el error cambia de forma.
    """
    fuente = CADENA.read_text(encoding="utf-8")
    usos = re.findall(r'"hash_documento"\s*:\s*([^,\n]+)', fuente)
    assert usos, "el guardián dejó de ver el código que vigila"
    desconocidas = [u.strip() for u in usos if not any(f in u for f in _FUENTES_LEGITIMAS)]
    assert not desconocidas, (
        f"fuente no reconocida para `hash_documento`: {desconocidas}. Si es la "
        "huella del documento, añádela a _FUENTES_LEGITIMAS; si es otra cosa, "
        "no puede llamarse así."
    )


def test_el_nombre_veraz_esta_en_uso() -> None:
    """El asiento del turno firmado publica el valor bajo su nombre real."""
    fuente = CADENA.read_text(encoding="utf-8")
    assert '"payload_firmado_sha256": sign_result["payload_sha256"]' in fuente, (
        "el asiento `firma.turno_firmado` dejó de publicar el hash del payload "
        "firmado bajo un nombre veraz"
    )
