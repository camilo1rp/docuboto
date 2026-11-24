# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contraseñas con argon2id y la política que antes aplicaba el realm (ADR-048).

Longitud mínima, historial de las últimas N y bloqueo temporal por intentos
fallidos. El bloqueo lo escribe `AuthService` en `auth_users.failed_logins` y
`locked_until`; aquí solo vive lo puro.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from docuboto.config import auth as settings

#: Tope de longitud: argon2 acepta cualquier tamaño, pero una contraseña de
#: megabytes es un ataque de coste, no una contraseña.
LONGITUD_MAXIMA = 256

_hasher = PasswordHasher()
#: Hash contra el que se verifica cuando la persona no existe o no tiene
#: contraseña: la respuesta tarda lo mismo y no delata qué usuarios hay.
_HASH_SEÑUELO = _hasher.hash("señuelo-de-tiempo-constante")


class PoliticaIncumplida(ValueError):
    """La contraseña nueva no cumple la política. `codigo` va al cuerpo del error."""

    def __init__(self, codigo: str, detalle: str) -> None:
        self.codigo = codigo
        self.detalle = detalle
        super().__init__(detalle)


def cifrar(clave: str) -> str:
    return _hasher.hash(clave)


def coincide(hash_guardado: str | None, clave: str) -> bool:
    """¿`clave` corresponde a `hash_guardado`? Sin hash, verifica contra el señuelo."""
    try:
        return _hasher.verify(hash_guardado or _HASH_SEÑUELO, clave) and bool(hash_guardado)
    except (VerificationError, InvalidHashError):
        return False


async def coincide_async(hash_guardado: str | None, clave: str) -> bool:
    """`coincide` fuera del bucle de eventos: argon2 tarda decenas de ms a propósito."""
    return await asyncio.to_thread(coincide, hash_guardado, clave)


def exigir_politica(clave: str, *, anteriores: Iterable[str] = ()) -> None:
    """Lanza `PoliticaIncumplida` si `clave` no sirve como contraseña nueva."""
    if len(clave) < settings.password_min_length:
        raise PoliticaIncumplida(
            "password_too_short",
            f"La contraseña debe tener al menos {settings.password_min_length} caracteres.",
        )
    if len(clave) > LONGITUD_MAXIMA:
        raise PoliticaIncumplida(
            "password_too_long",
            f"La contraseña no puede pasar de {LONGITUD_MAXIMA} caracteres.",
        )
    for previo in anteriores:
        if coincide(previo, clave):
            raise PoliticaIncumplida(
                "password_reused",
                f"La contraseña no puede repetir ninguna de las últimas "
                f"{settings.password_history}.",
            )
