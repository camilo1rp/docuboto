# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
TOTP (RFC 6238) — segundo factor para la firma personal (E06), elevando el
OTP-correo del Inc.2. Implementado con la stdlib (``hmac``/``hashlib``), NO
``pyotp`` — coherente con ``app/core/otp.py`` (signature-service) y con el
sellado RFC 3161 hecho a mano en el mismo servicio.

INVARIANTE RECTOR: el secreto se cifra en reposo con AESGCM
(``cryptography.hazmat``, NO Fernet — AESGCM permite Additional Authenticated
Data) y solo se descifra en memoria al enrolar/activar/verificar/revocar. El
secreto en claro NUNCA se persiste, ni se loguea, ni figura en ninguna
respuesta salvo el ``otpauth://`` URI de ``POST /enroll`` (una sola vez).

Parámetros fijos (D6 del contrato): HMAC-SHA1, 6 dígitos, periodo 30s, secreto
de 20 bytes (160 bit) — el perfil "clásico" de RFC 6238 / Google Authenticator,
el que todas las apps autenticadoras soportan sin configuración adicional.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECRET_LENGTH = 20  # 160 bit, RFC 6238 §5.1 recommendation for HMAC-SHA1
DIGITS = 6
PERIOD_SECONDS = 30
NONCE_LENGTH = 12  # AESGCM standard nonce size


# ---------------------------------------------------------------------------
# Secret generation / encoding
# ---------------------------------------------------------------------------


def generate_secret() -> bytes:
    """CSPRNG secret, 20 bytes (RFC 6238 §5.1)."""
    return secrets.token_bytes(SECRET_LENGTH)


def base32_encode(secret: bytes) -> str:
    """RFC 4648 base32, unpadded — the form authenticator apps expect in otpauth://."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def build_otpauth_uri(
    *,
    secret: bytes,
    issuer: str,
    account_name: str,
    digits: int = DIGITS,
    period: int = PERIOD_SECONDS,
) -> str:
    """Builds an ``otpauth://totp/{issuer}:{account}?...`` URI (Key URI Format).

    Revealed exactly once, in the ``POST /enroll`` response — never persisted
    or logged (the caller must send ``Cache-Control: no-store`` on that
    response).
    """
    label = urllib.parse.quote(f"{issuer}:{account_name}")
    params = urllib.parse.urlencode(
        {
            "secret": base32_encode(secret),
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": digits,
            "period": period,
        }
    )
    return f"otpauth://totp/{label}?{params}"


# ---------------------------------------------------------------------------
# HOTP/TOTP core (RFC 4226 / RFC 6238) — stdlib only
# ---------------------------------------------------------------------------


def _hotp(secret: bytes, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 HOTP value for a given counter (here, a TOTP timestep)."""
    msg = struct.pack(">Q", counter)
    digest = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code_int = truncated % (10**digits)
    return str(code_int).zfill(digits)


def current_timestep(period: int = PERIOD_SECONDS, now: float | None = None) -> int:
    """T = floor(unix_time / period) (RFC 6238 §4)."""
    t = now if now is not None else time.time()
    return int(t // period)


@dataclass(frozen=True, slots=True)
class TotpAttempt:
    """Classification of a single TOTP verification attempt.

    ``outcome`` is one of ``"valid"`` (code matches a timestep strictly
    greater than ``last_timestep`` — safe to accept and advance),
    ``"replay"`` (code matches a timestep at-or-before ``last_timestep`` —
    a previously-consumed or otherwise stale code), or ``"invalid"`` (no
    timestep in the skew window produces this code at all).
    """

    outcome: str
    timestep: int | None


def classify_totp_attempt(
    secret: bytes,
    code: str,
    *,
    last_timestep: int,
    period: int = PERIOD_SECONDS,
    digits: int = DIGITS,
    skew: int = 1,
    now: float | None = None,
) -> TotpAttempt:
    """Checks ``code`` against the ``[T-skew, T+skew]`` window (RFC 6238 §5.2).

    Distinguishes "wrong code" from "replay" (a code that WAS valid for some
    timestep in the window, but that timestep was already consumed) so the
    caller can report the correct ``motivo`` — a wrong code is a user typo;
    a replay is a captured/reused code and worth flagging distinctly.

    Constant-time comparison per candidate (``hmac.compare_digest``) — same
    discipline as ``signature-service/app/core/otp.py::verify_otp``.
    """
    t0 = current_timestep(period, now)
    for delta in range(-skew, skew + 1):
        tm = t0 + delta
        if hmac.compare_digest(_hotp(secret, tm, digits), code):
            if tm > last_timestep:
                return TotpAttempt("valid", tm)
            return TotpAttempt("replay", tm)
    return TotpAttempt("invalid", None)


# ---------------------------------------------------------------------------
# AESGCM encryption at rest
# ---------------------------------------------------------------------------


def build_aad(*, tenant_slug: str, user_id: str) -> bytes:
    """Additional Authenticated Data binding the ciphertext to its row/tenant.

    A ciphertext copied to another user's row, or replayed across tenants,
    fails to decrypt — AESGCM's AAD is authenticated but not encrypted, so
    this never touches the secret itself, only what the secret is allowed to
    be decrypted "as".
    """
    return f"tenant:{tenant_slug}:user:{user_id}:totp".encode()


def encrypt_secret(secret: bytes, *, key: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Returns ``(ciphertext, nonce)``. ``nonce`` is random, 12 bytes, per-row."""
    nonce = secrets.token_bytes(NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, secret, aad)
    return ciphertext, nonce


def decrypt_secret(ciphertext: bytes, *, nonce: bytes, key: bytes, aad: bytes) -> bytes:
    """Raises ``cryptography.exceptions.InvalidTag`` if ``aad`` doesn't match
    the one used at encryption (wrong row/tenant) or the ciphertext/nonce was
    tampered with."""
    return AESGCM(key).decrypt(nonce, ciphertext, aad)
