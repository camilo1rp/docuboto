# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma HMAC de una llamada del proveedor (PPI, `providerSignature`).

La cadena firmada es
``v1\\n{MÉTODO}\\n{ruta}\\n{instante}\\n{nonce}\\n{sha256 del cuerpo}``.
La ventana es de 300 segundos. El nonce de un solo uso lo reclama quien llama.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Mapping

VENTANA_S = 300
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_VERSION = re.compile(r"^1\.\d+\.\d+$")


def _valor(cabeceras: Mapping[str, str], nombre: str) -> str:
    buscado = nombre.lower()
    for clave, valor in cabeceras.items():
        if clave.lower() == buscado:
            return valor.strip()
    return ""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def comprobar(
    cuerpo: bytes,
    *,
    metodo: str,
    ruta: str,
    cabeceras: Mapping[str, str],
    secreto: bytes,
    ahora: float,
) -> str | None:
    """None si la firma cuadra. Si no, el motivo: version, instante, nonce, firma."""
    if not _VERSION.fullmatch(_valor(cabeceras, "PPI-Version")):
        return "version"
    marca = _valor(cabeceras, "PPI-Timestamp")
    try:
        instante = int(marca)
    except ValueError:
        return "instante"
    if abs(ahora - instante) > VENTANA_S:
        return "instante"
    nonce = _valor(cabeceras, "PPI-Nonce")
    if not _NONCE.fullmatch(nonce):
        return "nonce"
    recibida = _valor(cabeceras, "PPI-Signature")
    huella = hashlib.sha256(cuerpo).hexdigest()
    entrada = f"v1\n{metodo.upper()}\n{ruta}\n{marca}\n{nonce}\n{huella}".encode()
    esperada = "v1=" + _b64url(hmac.new(secreto, entrada, hashlib.sha256).digest())
    try:
        if not hmac.compare_digest(recibida, esperada):
            return "firma"
    except Exception:
        return "firma"
    return None


def plazo_nonce(marca: str, ahora: float) -> int:
    """Segundos que el nonce sigue dentro de la ventana."""
    return max(1, int(int(marca) + VENTANA_S - ahora))
