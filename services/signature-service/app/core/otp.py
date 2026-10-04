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
en ninguna respuesta — solo su HMAC-SHA256 (``otp_hash``) llega a la BD.

AREA-04-11 (migración 020, ``key_version``): el HMAC ya no usa el secreto raíz
``settings.signature_secret`` directo, el mismo de la firma nativa y de todas
las entidades. Con ``key_version=1`` la clave es una derivada por HKDF para el
propósito ``otp`` y la entidad (``app.core.secret_derivation``) y el mensaje
ata además el ``challenge_id``: el mismo código en dos desafíos produce hashes
distintos, y un hash copiado a otro desafío o a otra entidad no verifica. Los
desafíos anteriores (``key_version=0``) siguen verificándose con el esquema
original mientras no expiran (el OTP vive minutos).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid

from app.core.secret_derivation import (
    KEY_VERSION_ACTUAL,
    KEY_VERSION_HKDF,
    KEY_VERSION_LEGACY,
    PROPOSITO_OTP,
    derive_key,
)


def generate_otp(length: int = 6) -> str:
    """Genera un OTP numérico de ``length`` dígitos, zero-padded, con CSPRNG.

    ``secrets.randbelow`` (no ``random``) — inadecuado usar un PRNG no
    criptográfico para un secreto de un solo uso (RF-FIR-13).
    """
    upper_bound = 10**length
    value = secrets.randbelow(upper_bound)
    return str(value).zfill(length)


def _key_and_msg(
    otp: str,
    secret: str,
    *,
    key_version: int,
    tenant_slug: str,
    challenge_id: uuid.UUID,
) -> tuple[bytes, bytes]:
    if key_version == KEY_VERSION_LEGACY:
        return secret.encode(), otp.encode()
    if key_version == KEY_VERSION_HKDF:
        key = derive_key(secret, purpose=PROPOSITO_OTP, tenant_slug=tenant_slug)
        msg = json.dumps(
            ["orpycamcp/otp/v1", tenant_slug, str(challenge_id), otp],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return key, msg
    raise ValueError(f"key_version desconocida: {key_version!r}")


def hmac_otp(
    otp: str,
    secret: str,
    *,
    tenant_slug: str,
    challenge_id: uuid.UUID,
    key_version: int = KEY_VERSION_ACTUAL,
) -> str:
    """HMAC-SHA256 hex del OTP — lo único que se persiste (nunca el OTP en claro).

    Por defecto produce el esquema ACTUAL; ``key_version`` solo se pasa para
    reconstruir el de una fila anterior.
    """
    key, msg = _key_and_msg(
        otp, secret, key_version=key_version, tenant_slug=tenant_slug, challenge_id=challenge_id
    )
    return hmac.new(key, msg, digestmod=hashlib.sha256).hexdigest()


def verify_otp(
    otp: str,
    otp_hash: str,
    secret: str,
    *,
    key_version: int,
    tenant_slug: str,
    challenge_id: uuid.UUID,
) -> bool:
    """Compara en tiempo constante (evita timing-attack, mismo patrón que ``signer.verificar``).

    ``key_version`` es obligatorio: viene de la fila ``firma_challenge``. Una
    versión desconocida no verifica.
    """
    try:
        expected = hmac_otp(
            otp,
            secret,
            tenant_slug=tenant_slug,
            challenge_id=challenge_id,
            key_version=key_version,
        )
    except ValueError:
        return False
    return hmac.compare_digest(expected, otp_hash)


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
