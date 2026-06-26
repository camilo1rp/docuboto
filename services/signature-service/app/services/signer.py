# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Firma electrónica nativa pluggable (E06, ADR-016, RF-FIR-12).

La `firma_blob` es un HMAC-SHA256 que incorpora:
  - El SHA-256 del contenido (payload_sha256).
  - La identidad verificada del firmante (firmante_id, derivado del JWT).
  - El sello de tiempo del servidor (sello_tiempo, en ISO 8601 UTC).
  - Un secreto de servicio (settings.signature_secret).

Consecuencia: la firma NO es reproducible desde fuera aunque el payload y el
firmante_id sean públicos — se necesita el secreto del servicio.

Notas:
- Esto es firma ELECTRÓNICA, no firma digital cualificada (sin PKI/X.509).
- Un proveedor XAdES/PAdES/TSA real se conecta implementando `firmar`/`verificar`
  sin cambiar el contrato de la API ni el esquema de BD.
- `hmac.compare_digest` evita timing-attack en la verificación.
"""

from __future__ import annotations

import hashlib
import hmac


def firmar(
    payload: bytes,
    firmante_id: str,
    sello_tiempo: str,
    secret: str,
) -> dict:
    """
    Produce la firma electrónica nativa.

    Args:
        payload:      Bytes del contenido a firmar (XML, hash, etc.).
        firmante_id:  UUID del firmante, derivado del JWT validado.
        sello_tiempo: Timestamp ISO 8601 UTC generado por el servidor.
        secret:       Secreto HMAC del servicio (settings.signature_secret).

    Returns:
        Dict con payload_sha256, firma_blob y provider.
    """
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    msg = f"{payload_sha256}|{firmante_id}|{sello_tiempo}".encode()
    firma_blob = hmac.new(secret.encode(), msg, digestmod=hashlib.sha256).hexdigest()
    return {
        "payload_sha256": payload_sha256,
        "firma_blob": firma_blob,
        "provider": "nativa",
    }


def verificar(
    payload: bytes,
    firmante_id: str,
    firma_blob: str,
    sello_tiempo: str,
    secret: str,
) -> bool:
    """
    Verifica una firma electrónica nativa.

    Recomputa el HMAC con los mismos parámetros almacenados en la fila `firma`
    (firmante_id + sello_tiempo) y compara con timing-safe compare_digest.
    """
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    msg = f"{payload_sha256}|{firmante_id}|{sello_tiempo}".encode()
    expected = hmac.new(secret.encode(), msg, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, firma_blob)
