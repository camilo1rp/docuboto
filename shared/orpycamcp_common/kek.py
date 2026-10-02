# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cifrado en reposo con KEK versionada (AES-256-GCM + AAD) — esquema común.

Es el esquema que auth-service ya usaba para la clave de firma personal
(``app/core/signing_keys.py``), extraído aquí para que un segundo custodio de
secretos (el HMAC de los webhooks de notification-service) use EXACTAMENTE el
mismo y no una copia que divergería:

- **AEAD con AAD**: el ciphertext queda atado a quien lo posee (tenant + objeto
  + propósito). Descifrar con otro AAD lanza ``InvalidTag``: una fila copiada a
  otro tenant u otro objeto no descifra.
- **Nonce aleatorio de 12 bytes por cifrado**, jamás reutilizado con una KEK
  (catastrófico en GCM): un re-wrap acuña uno nuevo.
- **Llavero versionado**: la versión 1 sale del campo legacy; las >= 2 de un JSON.
  Cada fila lleva la versión de la KEK que la cifró, así que durante una rotación
  conviven varias y se re-envuelven por lotes. Fail-closed: una versión sin KEK
  cargada NUNCA cae a otra.

Requiere `cryptography` (extra `kek` de la librería; el servicio que lo importe lo
declara en su requirements). Este módulo es solo criptografía pura (sin configuración ni base de datos): cada
servicio aporta sus ajustes y su AAD.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_LENGTH = 12  # tamaño estándar de nonce de AES-GCM


def decode_aes256(b64: str, label: str) -> bytes:
    """Decodifica un base64 a exactamente 32 bytes (AES-256) o lanza ``RuntimeError``.

    Nunca cae a una clave derivada o implícita; el mensaje nombra la variable
    (``label``) pero jamás incluye el valor recibido.
    """
    try:
        key = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"{label} no es base64 válido") from exc
    if len(key) != 32:
        raise RuntimeError(f"{label} debe decodificar a 32 bytes (AES-256)")
    return key


def build_keyring(
    v1_b64: str,
    extra: Mapping[int, str],
    *,
    v1_label: str,
    extra_label: str,
) -> dict[int, bytes]:
    """Llavero completo ``{version: 32B}``. La v1 sale del campo legacy ``v1_b64``;
    las v>=2 del mapa ``extra``. Fail-closed por entrada; la versión <=1 en
    ``extra`` está prohibida (reservada al campo legacy)."""
    if not v1_b64:
        raise RuntimeError(f"{v1_label} no configurada")
    ring: dict[int, bytes] = {1: decode_aes256(v1_b64, v1_label)}
    for raw_ver, b64 in extra.items():
        ver = int(raw_ver)
        if ver <= 1:
            raise RuntimeError(
                f"{extra_label}: la versión <=1 está reservada al campo "
                f"{v1_label} (no redefinir aquí)"
            )
        ring[ver] = decode_aes256(b64, f"{extra_label}[{ver}]")
    return ring


def key_for_version(ring: Mapping[int, bytes], version: int) -> bytes:
    """KEK de una versión concreta. Fail-closed si no está cargada — NUNCA se
    descifra con otra KEK."""
    if version not in ring:
        raise RuntimeError(
            f"KEK versión {version} no cargada (fail-closed) — no se puede descifrar"
        )
    return ring[version]


def seal(plaintext: bytes, *, key: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Devuelve ``(ciphertext, nonce)``; nonce aleatorio de 12 bytes por cifrado."""
    nonce = secrets.token_bytes(NONCE_LENGTH)
    return AESGCM(key).encrypt(nonce, plaintext, aad), nonce


def unseal(ciphertext: bytes, *, nonce: bytes, key: bytes, aad: bytes) -> bytes:
    """Lanza ``cryptography.exceptions.InvalidTag`` si el AAD, la KEK, el nonce o
    el ciphertext no coinciden."""
    return AESGCM(key).decrypt(nonce, ciphertext, aad)
