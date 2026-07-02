# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Generación y verificación de OTP de un solo uso (E06 Inc.2, RF-FIR-13).

INVARIANTE RECTOR: el OTP en claro NUNCA se persiste, ni se loguea, ni viaja
en ninguna respuesta — solo su HMAC-SHA256 (``otp_hash``) llega a la BD.  El
secreto usado es el mismo ``settings.signature_secret`` que ya protege la
firma nativa (``app/services/signer.py``, D-01): un solo secreto de servicio
para todo lo que necesita ser "no reproducible sin el servidor".

Análogo de ``app/services/signer.py``: allí el HMAC ata contenido+firmante+
sello; aquí ata únicamente el código emitido, sin nada más pegado — el
enlace con el documento y el firmante vive en las columnas de
``firma_challenge`` (``hash_documento``, ``firmante_id``), no en el HMAC.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_otp(length: int = 6) -> str:
    """Genera un OTP numérico de ``length`` dígitos, zero-padded, con CSPRNG.

    ``secrets.randbelow`` (no ``random``) — inadecuado usar un PRNG no
    criptográfico para un secreto de un solo uso (RF-FIR-13).
    """
    upper_bound = 10**length
    value = secrets.randbelow(upper_bound)
    return str(value).zfill(length)


def hmac_otp(otp: str, secret: str) -> str:
    """HMAC-SHA256 hex del OTP — lo único que se persiste (nunca el OTP en claro)."""
    return hmac.new(secret.encode(), otp.encode(), digestmod=hashlib.sha256).hexdigest()


def verify_otp(otp: str, otp_hash: str, secret: str) -> bool:
    """Compara en tiempo constante (evita timing-attack, mismo patrón que ``signer.verificar``)."""
    return hmac.compare_digest(hmac_otp(otp, secret), otp_hash)


def mask_email(email: str) -> str:
    """Enmascara un email para exhibición segura: ``jdoe@x.gov.co`` → ``j***@x.gov.co``.

    Fallback ``***`` cuando no hay ``@`` (entrada inesperada de auth-service /me) —
    nunca se propaga el valor sin enmascarar.
    """
    local, sep, domain = email.partition("@")
    if not sep or not domain:
        return "***"
    prefix = local[:1] or "*"
    return f"{prefix}***@{domain}"
