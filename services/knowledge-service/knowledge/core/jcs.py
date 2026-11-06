# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""JSON canónico de RFC 8785 para los tipos que trae una declaración.

Objetos, listas, cadenas, enteros, booleanos y null. Un número con parte
fraccionaria no entra: la declaración no los usa, y serializarlos mal
cambiaría la huella.
"""

from __future__ import annotations

from typing import Any


def canonico(valor: Any) -> bytes:
    """Octetos UTF-8 del JSON canónico. Lanza ``TypeError`` si hay un tipo ajeno."""
    return _emitir(valor).encode("utf-8")


def _emitir(valor: Any) -> str:
    if valor is None:
        return "null"
    if valor is True:
        return "true"
    if valor is False:
        return "false"
    if isinstance(valor, int) and not isinstance(valor, bool):
        return str(valor)
    if isinstance(valor, str):
        return _cadena(valor)
    if isinstance(valor, list):
        return "[" + ",".join(_emitir(pieza) for pieza in valor) + "]"
    if isinstance(valor, dict):
        claves = sorted(valor, key=lambda clave: clave.encode("utf-16-be"))
        pares = [f"{_cadena(clave)}:{_emitir(valor[clave])}" for clave in claves]
        return "{" + ",".join(pares) + "}"
    raise TypeError("tipo ajeno al canonico")


def _cadena(texto: str) -> str:
    salida = ['"']
    for caracter in texto:
        codigo = ord(caracter)
        if caracter == '"':
            salida.append('\\"')
        elif caracter == "\\":
            salida.append("\\\\")
        elif caracter == "\b":
            salida.append("\\b")
        elif caracter == "\t":
            salida.append("\\t")
        elif caracter == "\n":
            salida.append("\\n")
        elif caracter == "\f":
            salida.append("\\f")
        elif caracter == "\r":
            salida.append("\\r")
        elif codigo < 0x20:
            salida.append(f"\\u{codigo:04x}")
        else:
            salida.append(caracter)
    salida.append('"')
    return "".join(salida)
