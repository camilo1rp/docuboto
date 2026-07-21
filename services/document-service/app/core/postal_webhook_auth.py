# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Verificación HMAC del webhook ENTRANTE del operador postal (E20 F5).

Simétrico al webhook SALIENTE de ADR-018 (notification-service firma con
HMAC-SHA256 y el receptor verifica); aquí OrpycaMCP es el receptor: el operador
firma el cuerpo crudo con el secreto compartido per-tenant/per-operador
(`postal_operator_credential`) y este servicio verifica la cabecera
`X-OrpycaMCP-Signature: sha256=<hexdigest>`.

El secreto vive en el schema del tenant: una firma válida PRUEBA que el emisor
conoce el secreto de ESE tenant, así que el `tenant_slug` del path no puede
usarse para falsificar callbacks de otro tenant (necesitaría su secreto).
"""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """True sólo si `signature_header` es el HMAC-SHA256 de `body` con `secret`.

    Comparación en tiempo constante (`hmac.compare_digest`). Fail-closed: cabecera
    ausente, sin el prefijo esperado o secreto vacío → False.
    """
    if not secret or not signature_header or not signature_header.startswith(_PREFIX):
        return False
    provided = signature_header[len(_PREFIX) :]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)
