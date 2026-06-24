# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Proveedor de firma pluggable (E06, ADR-016).

`nativa` (por defecto): firma ELECTRÓNICA — hash SHA-256 del contenido + identidad
del firmante (sin PKI). NO es firma digital cualificada. Un proveedor XAdES/PAdES
con certificado X.509 + TSA se conecta implementando `firmar`/`verificar` sin
cambiar el contrato de la API ni el esquema.
"""

from __future__ import annotations

import hashlib


def _blob(payload: bytes, firmante_id: str) -> str:
    return hashlib.sha256(payload + b"|" + firmante_id.encode()).hexdigest()


def firmar(payload: bytes, firmante_id: str) -> dict:
    return {
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "firma_blob": _blob(payload, firmante_id),
        "provider": "nativa",
    }


def verificar(payload: bytes, firmante_id: str, firma_blob: str) -> bool:
    return _blob(payload, firmante_id) == firma_blob
