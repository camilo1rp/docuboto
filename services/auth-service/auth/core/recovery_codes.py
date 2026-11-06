# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Códigos de recuperación del 2FA TOTP (E06).

Primitivas puras: generación, normalización y hash. No tocan la base de datos
ni FastAPI — se prueban solas y el servicio las compone.

**Por qué scrypt de la biblioteca estándar y no una dependencia nueva.** El
requisito real es que un volcado de la tabla no permita autenticarse; para eso
basta una KDF con coste ajustable y sal por código, y `hashlib.scrypt` la
proporciona sin añadir superficie de dependencias a un servicio que ya custodia
secretos TOTP y claves de firma. Los parámetros de abajo son los de RFC 7914
para uso interactivo (~16 MiB, unos pocos ms por verificación); un código de
recuperación es un secreto de 100 bits de entropía, así que el coste de la KDF
no es la línea de defensa principal, sino un seguro contra un volcado.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# Alfabeto sin caracteres ambiguos a la vista o al dictado (0/O, 1/I/L): estos
# códigos se imprimen y se copian a mano, así que un `0` que se lee como `O` es
# un modo de fallo real y evitable.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_GROUP_LEN = 5
_GROUPS = 4  # 20 caracteres => ~98 bits de entropía sobre este alfabeto

RECOVERY_CODE_COUNT = 10
"""Cuántos códigos se emiten por lote. Diez es el consenso de la industria: hay
que cubrir varias pérdidas de dispositivo sin convertir la lista en algo que
nadie guarda."""

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16


def generate_recovery_code() -> str:
    """Un código en el formato de presentación ``XXXXX-XXXXX-XXXXX-XXXXX``.

    Los guiones son de presentación, no de contenido: `normalize_recovery_code`
    los descarta, así que el usuario puede teclearlos o no.
    """
    grupos = [
        "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN)) for _ in range(_GROUPS)
    ]
    return "-".join(grupos)


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    return [generate_recovery_code() for _ in range(count)]


def normalize_recovery_code(code: str) -> str:
    """Forma canónica sobre la que se calcula el hash.

    Descarta guiones, espacios y mayúsculas/minúsculas. Sin esto, un usuario que
    teclea el código sin guiones —o desde un móvil que autocapitaliza— vería
    rechazado un código correcto, y ese falso negativo empuja justo hacia la
    intervención manual sobre la base que esta funcionalidad existe para evitar.
    """
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_recovery_code(code: str, *, salt: bytes) -> bytes:
    """scrypt sobre la forma canónica del código."""
    return hashlib.scrypt(
        normalize_recovery_code(code).encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )


def new_salt() -> bytes:
    return secrets.token_bytes(_SALT_BYTES)


def verify_recovery_code(code: str, *, salt: bytes, expected_hash: bytes) -> bool:
    """Comparación en tiempo constante (`hmac.compare_digest`).

    Un `==` sobre bytes corta en el primer byte distinto y filtra por tiempo
    cuántos coinciden. Aquí importa menos que en un secreto largo, pero es
    gratis y la alternativa no tiene ninguna ventaja.
    """
    return hmac.compare_digest(hash_recovery_code(code, salt=salt), expected_hash)
