# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
Derivación de claves por entidad y por propósito (AREA-04-11).

Antes, UN solo ``settings.signature_secret`` firmaba el HMAC de la firma nativa
y el HMAC del OTP, y era el mismo para todas las entidades: que se filtrara una
sola vez comprometía a todas, y una fila ``firma`` copiada a otro schema (una
restauración, un import) seguía verificando porque el mensaje no nombraba a la
entidad.

Ahora la clave de cada uso sale de **HKDF-SHA256** (RFC 5869) sobre ese secreto
raíz, con ``info = "orpycamcp/{propósito}:{tenant_slug}"``. El secreto raíz no
cambia ni se rota: lo que cambia es que cada (propósito, entidad) tiene su
propia clave derivada, y las dos no se pueden intercambiar.

``key_version`` (columna de ``firma`` y ``firma_challenge``, migración 020) dice
con qué esquema se produjo cada fila:

- ``0`` (``KEY_VERSION_SIN_HMAC``): «sin HMAC de servicio». Es el relleno de las
  filas no nativas (XAdES), que no tienen este MAC. El esquema HMAC anterior a
  la 020 (clave raíz directa, mensaje sin entidad ni objeto) se **retiró** en B9:
  una fila nativa con 0 ya no verifica, ni se produce nada nuevo así. Desde la
  024 la columna ya no tiene DEFAULT: cada INSERT declara el valor.
- ``1`` (``KEY_VERSION_HKDF``): clave derivada + mensaje que ata entidad, tipo y
  objeto. Es lo que se firma desde la 020.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_VERSION_SIN_HMAC = 0
KEY_VERSION_HKDF = 1
#: Versión con la que se produce todo lo nuevo.
KEY_VERSION_ACTUAL = KEY_VERSION_HKDF

#: Propósitos conocidos. Cerrado a propósito: un propósito nuevo se declara
#: aquí, no se improvisa en el sitio de uso (dos usos con la misma etiqueta
#: compartirían clave, que es exactamente lo que esto evita).
PROPOSITO_FIRMA_NATIVA = "firma_nativa"
PROPOSITO_OTP = "otp"
_PROPOSITOS = frozenset({PROPOSITO_FIRMA_NATIVA, PROPOSITO_OTP})


def derive_key(root_secret: str, *, purpose: str, tenant_slug: str) -> bytes:
    """Clave de 32 bytes para (``purpose``, ``tenant_slug``), derivada del secreto raíz.

    Determinista: misma entrada, misma clave —es lo que permite verificar—.
    Un ``tenant_slug`` vacío se rechaza: una clave «de ninguna entidad» sería
    justo la clave compartida que esto viene a eliminar, y llegaría en silencio
    desde un llamante que olvidó pasarla.
    """
    if purpose not in _PROPOSITOS:
        raise ValueError(f"propósito de clave desconocido: {purpose!r}")
    if not tenant_slug:
        raise ValueError("tenant_slug es obligatorio para derivar la clave")
    if not root_secret:
        raise ValueError("el secreto raíz no puede estar vacío")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"orpycamcp/{purpose}:{tenant_slug}".encode(),
    ).derive(root_secret.encode())
